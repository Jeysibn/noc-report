"""Structured output validation (master plan §27 step 13). Validates the
sandbox's `result.json` against the contract the invoked skill promises,
before it's ever uploaded to MinIO or treated as a real result — a bad or
truncated model/stand-in output must fail loudly here, not surface as a
malformed AnalysisRun/Report downstream.

Skill Runtime mission Phase 4: this used to hard-code each skill's field
names (summary_en/key_finds/secondary_finds/likely_cause_en/... for
log-triage-summary; overview_en/cross_incident_findings_en/... for
daily-alert-report) directly in Python — a second, hand-maintained copy of
each skill's `output.schema.json` contract that could (and did) drift from
the actual schema file. `validate_output` is now generic: it loads the
skill's own output.schema.json (from the same per-job materialized
SkillSnapshot directory the sandbox itself validated against — see
`bridge/noc_bridge/skill_registry.materialize_snapshot` and
`sandbox/entrypoint.py::_load_output_schema`) and runs a standard
`jsonschema` validation. A skill's output format can now change by
editing its schema file alone; no generic bridge code needs to change.

`validate_merged_daily_report` is intentionally NOT schema-driven the same
way: it checks the *deterministically-merged* report structure (title/
overview/sections, each section carrying through screenshots/links/
existing analysis verbatim) that `service.py::_merge_daily_report`
assembles from the frozen snapshot plus Claude's compact AI output — a
structure `docx_render.py` depends on today, not a skill's own output
contract. The Skill Runtime mission's ReportDocument work (Phase 8/9) is
what's expected to eventually replace this hand-written check with a
generic renderer-facing schema; until then it stays declarative here,
same as before.
"""
from __future__ import annotations

import json
import pathlib

import jsonschema


class OutputValidationError(ValueError):
    pass


def validate_against_schema(output: dict, schema: dict, *, label: str = "output") -> None:
    """Generic JSON Schema validation, wrapping jsonschema's own exception
    in OutputValidationError so callers (and noc_bridge.failures'
    classify_failure) keep dealing with one exception type regardless of
    which skill or schema failed."""
    try:
        jsonschema.validate(instance=output, schema=schema)
    except jsonschema.ValidationError as exc:
        raise OutputValidationError(f"{label} failed schema validation: {exc.message}") from exc
    except jsonschema.SchemaError as exc:
        raise OutputValidationError(f"{label}'s own schema is invalid: {exc.message}") from exc


def validate_output(
    job_type: str,
    output: dict | None,
    *,
    skill_name: str,
    skills_dir: pathlib.Path,
) -> None:
    """Validates the sandbox's raw structured output against the exact
    skill's output.schema.json found under `skills_dir` — the same
    directory Phase 1 materialized this job's exact SkillSnapshot content
    into (or the live checkout, for a job with no skill_hash at all)."""
    if output is None:
        raise OutputValidationError(f"sandbox produced no result.json for job_type={job_type!r}")
    schema_path = skills_dir / skill_name / "output.schema.json"
    try:
        schema = json.loads(schema_path.read_text())
    except FileNotFoundError as exc:
        raise OutputValidationError(f"no output schema found for skill {skill_name!r} at {schema_path}") from exc
    validate_against_schema(output, schema, label=f"{job_type} output")


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


def validate_merged_daily_report(output: dict) -> None:
    """Validates the final, deterministically-merged daily report
    structure (title/overview/sections, each carrying through screenshots/
    links/existing analysis verbatim) — the same contract
    bridge/noc_bridge/docx_render.py has always depended on. See this
    module's docstring for why this one stays hand-written rather than
    schema-driven."""
    _validate_daily_report(output)
