"""Structured output validation (master plan §27 step 13). Validates the
sandbox's `result.json` against the contract the invoked skill promises,
before it's ever uploaded to MinIO or treated as a real result — a bad or
truncated model/stand-in output must fail loudly here, not surface as a
malformed AnalysisRun/Report downstream.

`log_triage` validates against `skills/log-triage-summary/SKILL.md`'s
contract: bilingual (Chinese/English) summary plus a Key Finds/Secondary
Finds breakdown with per-pattern count/percentage. `daily_report` validates
against `skills/daily-alert-report/SKILL.md`'s contract: bilingual overview
plus a `sections` list (one per incident) each carrying a nullable, carried-
through `analysis` object in that same shape.
"""
from __future__ import annotations


class OutputValidationError(ValueError):
    pass


_SEVERITY_LEVELS = {"low", "medium", "high", "critical"}


def _validate_find_entry(entry: object, path: str) -> None:
    if not isinstance(entry, dict):
        raise OutputValidationError(f"{path} must be an object")
    for field in ("label_en", "label_zh", "detail_en", "detail_zh"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise OutputValidationError(f"{path}.{field} must be a non-empty string")
    count = entry.get("count")
    if count is not None and not isinstance(count, int):
        raise OutputValidationError(f"{path}.count must be an integer or null")
    percentage = entry.get("percentage")
    if percentage is not None and not isinstance(percentage, (int, float)):
        raise OutputValidationError(f"{path}.percentage must be a number or null")


def _validate_analysis_object(analysis: dict, path: str) -> None:
    required = [
        "summary_en", "summary_zh", "key_finds", "secondary_finds",
        "likely_cause_en", "likely_cause_zh",
        "recommended_action_en", "recommended_action_zh",
        "severity_signal", "confidence",
    ]
    missing = [field for field in required if field not in analysis]
    if missing:
        raise OutputValidationError(f"{path} missing fields: {missing}")

    for field in (
        "summary_en", "summary_zh", "likely_cause_en", "likely_cause_zh",
        "recommended_action_en", "recommended_action_zh",
    ):
        if not isinstance(analysis[field], str) or not analysis[field].strip():
            raise OutputValidationError(f"{path}.{field} must be a non-empty string")

    if analysis["severity_signal"] not in _SEVERITY_LEVELS:
        raise OutputValidationError(
            f"{path}.severity_signal must be one of {sorted(_SEVERITY_LEVELS)}, got {analysis['severity_signal']!r}"
        )

    confidence = analysis["confidence"]
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        raise OutputValidationError(f"{path}.confidence must be a float in [0.0, 1.0], got {confidence!r}")

    key_finds = analysis["key_finds"]
    if not isinstance(key_finds, list) or len(key_finds) == 0:
        raise OutputValidationError(f"{path}.key_finds must be a non-empty list")
    for i, entry in enumerate(key_finds):
        _validate_find_entry(entry, f"{path}.key_finds[{i}]")

    secondary_finds = analysis["secondary_finds"]
    if not isinstance(secondary_finds, list):
        raise OutputValidationError(f"{path}.secondary_finds must be a list")
    for i, entry in enumerate(secondary_finds):
        _validate_find_entry(entry, f"{path}.secondary_finds[{i}]")


def _validate_log_triage(output: dict) -> None:
    _validate_analysis_object(output, "output")


def _validate_daily_report_ai_output(output: dict) -> None:
    """AI cost-optimization mission Phase 2, Issue 6: validates the
    COMPACT output Claude actually produces now (see
    skills/daily-alert-report/SKILL.md and sandbox/entrypoint.py's
    _SCHEMAS["daily-alert-report"]) — just the shift-level overview and
    any real cross-incident correlation, not the full merged report
    (title/sections/screenshots/etc, which service.py assembles
    deterministically afterward and validates separately via
    `_validate_daily_report`)."""
    required = [
        "overview_en", "overview_zh",
        "cross_incident_findings_en", "cross_incident_findings_zh",
    ]
    missing = [field for field in required if field not in output]
    if missing:
        raise OutputValidationError(f"daily_report AI output missing fields: {missing}")
    for field in required:
        if not isinstance(output[field], str) or not output[field].strip():
            raise OutputValidationError(f"{field} must be a non-empty string")


def _validate_daily_report(output: dict) -> None:
    required = ["title", "overview_en", "overview_zh", "sections"]
    missing = [field for field in required if field not in output]
    if missing:
        raise OutputValidationError(f"daily_report output missing fields: {missing}")

    for field in ("title", "overview_en", "overview_zh"):
        if not isinstance(output[field], str) or not output[field].strip():
            raise OutputValidationError(f"{field} must be a non-empty string")

    sections = output["sections"]
    if not isinstance(sections, list):
        raise OutputValidationError("sections must be a list")

    section_required = ["incident_display_id", "title", "status"]
    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            raise OutputValidationError(f"sections[{i}] must be an object")
        missing = [field for field in section_required if field not in section]
        if missing:
            raise OutputValidationError(f"sections[{i}] missing fields: {missing}")
        for field in section_required:
            if not isinstance(section[field], str) or not section[field].strip():
                raise OutputValidationError(f"sections[{i}].{field} must be a non-empty string")

        analysis = section.get("analysis")
        if analysis is not None:
            _validate_analysis_object(analysis, f"sections[{i}].analysis")


_VALIDATORS = {
    "log_triage": _validate_log_triage,
    # AI cost-optimization mission Phase 2, Issue 6: this validates the
    # sandbox's raw output — now the COMPACT AI output, not the full
    # merged report. The full merged report (built deterministically in
    # service.py from this plus the frozen snapshot) is validated
    # separately via `validate_merged_daily_report` before rendering.
    "daily_report": _validate_daily_report_ai_output,
}


def validate_merged_daily_report(output: dict) -> None:
    """Validates the final, deterministically-merged daily report
    structure (title/overview/sections, each carrying through screenshots/
    links/existing analysis verbatim) — the same contract
    bridge/noc_bridge/docx_render.py has always depended on."""
    _validate_daily_report(output)


def validate_output(job_type: str, output: dict | None) -> None:
    if output is None:
        raise OutputValidationError("sandbox produced no result.json")
    validator = _VALIDATORS.get(job_type)
    if validator is None:
        raise OutputValidationError(f"no output validator registered for job_type={job_type!r}")
    validator(output)
