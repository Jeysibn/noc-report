"""Structured output validation for runtime artifacts. Validates a result
document against the contract the invoked skill promises, before it is
uploaded to MinIO or treated as a real result — a bad or truncated output
must fail loudly here, not surface as a
malformed AnalysisRun/Report downstream.

Skill Runtime validation is generic: it loads the selected skill's own
`output.schema.json` from the per-job materialized directory and runs standard
JSON Schema validation. A skill's output format can change by editing its
schema file alone; generic runtime code does not define
result fields.

The historical `daily_report_docx` compatibility assembler has its own
validation in `report_validation.py`. Generic runtime validation does not
inspect report fields or semantics; current `report-document-v1` reports use
the Daily Alert Report skill's narrative-only schema.
"""
from __future__ import annotations

import json
import math
import pathlib
import re

import jsonschema


class OutputValidationError(ValueError):
    pass


_SENTENCE_ENDINGS = re.compile(r"[.!?。！？]")
_LINKING_EVIDENCE = re.compile(
    r"(?:trace|request\s*(?:id|identifier)|correlation\s*(?:id|identifier)|"
    r"nested\s+exception|caller|callee|same\s+dependency|explicit(?:ly)?\s+linked|"
    r"链路|请求\s*ID|关联\s*ID|嵌套异常|调用方|被调用方|同一依赖|明确关联)",
    re.IGNORECASE,
)
_SHARED_CAUSATION = re.compile(
    r"(?:share(?:s|d)?\s+(?:one\s+|a\s+)?(?:single\s+)?(?:root\s+)?cause|"
    r"common\s+(?:root\s+)?cause|single\s+root\s+cause|"
    r"(?:both|these)\s+(?:failures|errors|clusters|patterns)\s+(?:are|were)\s+caused\s+by|"
    r"共同.{0,8}(?:根因|原因)|同一.{0,8}(?:根因|原因)|单一.{0,8}(?:根因|原因))",
    re.IGNORECASE,
)
_SPECULATIVE_CAUSATION = re.compile(
    r"(?:probably|likely|possibly|may be|might be|could be)\s+(?:caused\s+by|due\s+to|related\s+to)|"
    r"可能(?:由于|是由|与.+相关)",
    re.IGNORECASE,
)
_CAUSATION_NEGATION = re.compile(
    r"(?:cannot|can't|can not|no evidence|not established|not confirmed|"
    r"cannot be confirmed|无法|不能|未发现|没有证据|不能确认|未确认)",
    re.IGNORECASE,
)


def _sentence_count(value: str) -> int:
    """Count operator-readable sentence boundaries conservatively."""
    return max(1, len(_SENTENCE_ENDINGS.findall(value)))


def _analysis_text(result: dict) -> str:
    values = [result.get("summary_en"), result.get("summary_zh")]
    for group_name in ("key_finds", "secondary_finds"):
        for finding in result.get(group_name) or []:
            if isinstance(finding, dict):
                values.extend(
                    finding.get(field)
                    for field in ("label_en", "label_zh", "detail_en", "detail_zh")
                )
    return "\n".join(value for value in values if isinstance(value, str))


def _compact_analysis_text(value: str) -> str:
    return re.sub(r"\W+", "", value.casefold(), flags=re.UNICODE)


def _validate_repetition(result: dict, *, label: str) -> None:
    """Reject copied long-form evidence while allowing normal label overlap.

    A summary may name a finding, but it must not reproduce a complete finding
    explanation. This deliberately checks only long normalized detail strings
    so short technical labels and unavoidable bilingual terminology remain
    valid.
    """
    for suffix in ("_zh", "_en"):
        summary = result.get(f"summary{suffix}")
        if not isinstance(summary, str):
            continue
        compact_summary = _compact_analysis_text(summary)
        seen_details: set[str] = set()
        for group_name in ("key_finds", "secondary_finds"):
            for index, finding in enumerate(result.get(group_name) or []):
                if not isinstance(finding, dict):
                    continue
                detail = finding.get(f"detail{suffix}")
                if not isinstance(detail, str):
                    continue
                compact_detail = _compact_analysis_text(detail)
                if len(compact_detail) < 32:
                    continue
                path = f"{label}.{group_name}[{index}].detail{suffix}"
                if compact_detail in compact_summary:
                    raise OutputValidationError(
                        f"{path} repeats its full explanation in summary{suffix}"
                    )
                if compact_detail in seen_details:
                    raise OutputValidationError(f"{path} repeats another finding detail")
                seen_details.add(compact_detail)


def _validate_finding_detail_density(result: dict, *, label: str) -> None:
    """Keep primary explanations substantive and secondary explanations brief."""
    for group_name, maximum_length, maximum_sentences in (
        ("key_finds", 600, 3),
        ("secondary_finds", 320, 1),
    ):
        for index, finding in enumerate(result.get(group_name) or []):
            if not isinstance(finding, dict):
                continue
            path = f"{label}.{group_name}[{index}]"
            for field in ("detail_zh", "detail_en"):
                value = finding.get(field)
                if not isinstance(value, str):
                    continue
                if len(value) > maximum_length:
                    raise OutputValidationError(
                        f"{path}.{field} is too long for {group_name}"
                    )
                if _sentence_count(value) > maximum_sentences:
                    raise OutputValidationError(
                        f"{path}.{field} must be no more than {maximum_sentences} sentence"
                    )


def _validate_causation_language(result: dict, *, label: str) -> None:
    """Reject unsupported shared-cause assertions while allowing evidence-bound
    statements and explicit uncertainty. Temporal coexistence alone is not a
    causal link, so the validator requires a linking-evidence term whenever a
    result asserts that independent patterns share a cause."""
    text = _analysis_text(result)
    shared_claim = _SHARED_CAUSATION.search(text)
    speculative_claim = _SPECULATIVE_CAUSATION.search(text)
    if not shared_claim and not speculative_claim:
        return
    if _CAUSATION_NEGATION.search(text):
        return
    if not _LINKING_EVIDENCE.search(text):
        raise OutputValidationError(
            f"{label} contains an unsupported causal assertion; "
            "temporal coexistence is not causal evidence"
        )


def validate_log_triage_result(result: dict, *, label: str = "log_triage output") -> None:
    """Validate the semantic invariants of the active Log Triage contract.

    JSON Schema checks field shape. This second seam checks the invariants that
    Schema cannot express portably: one bilingual finding object owns one
    identity/classification, counts are matching physical log entries, and a
    percentage agrees with its count and total after deterministic runtime
    reconciliation.

    Legacy snapshots may still contain cause/action fields. The caller keeps
    those immutable rows on the historical adapter and only invokes this
    validator for the active schema.
    """
    total = result.get("total_entries")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise OutputValidationError(f"{label}.total_entries must be a non-negative integer")

    for field in ("summary_zh", "summary_en"):
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OutputValidationError(f"{label}.{field} must be a non-empty string")
        if len(value) > 800:
            raise OutputValidationError(f"{label}.{field} is too long for Short Summary")
        sentence_count = _sentence_count(value)
        if not 2 <= sentence_count <= 4:
            raise OutputValidationError(
                f"{label}.{field} must contain 2-4 concise sentences; got {sentence_count}"
            )

    seen_ids: set[str] = set()
    seen_finding_ids: set[str] = set()
    for group_name in ("key_finds", "secondary_finds"):
        findings = result.get(group_name)
        if not isinstance(findings, list):
            raise OutputValidationError(f"{label}.{group_name} must be a list")
        for index, finding in enumerate(findings):
            path = f"{label}.{group_name}[{index}]"
            if not isinstance(finding, dict):
                raise OutputValidationError(f"{path} must be an object")
            finding_id = finding.get("id")
            if not isinstance(finding_id, str) or not finding_id.strip():
                raise OutputValidationError(f"{path}.id must be a non-empty string")
            if finding_id in seen_finding_ids:
                raise OutputValidationError(f"duplicate finding id: {finding_id}")
            seen_finding_ids.add(finding_id)
            for field in ("label_zh", "label_en", "detail_zh", "detail_en"):
                if not isinstance(finding.get(field), str) or not finding[field].strip():
                    raise OutputValidationError(f"{path}.{field} must be a non-empty string")

            pattern_ids = finding.get("pattern_ids")
            if not isinstance(pattern_ids, list) or not pattern_ids:
                raise OutputValidationError(f"{path}.pattern_ids must be a non-empty list")
            if any(not isinstance(pattern_id, str) or not pattern_id for pattern_id in pattern_ids):
                raise OutputValidationError(f"{path}.pattern_ids contains an invalid ID")
            if len(pattern_ids) != len(set(pattern_ids)):
                raise OutputValidationError(f"{path}.pattern_ids contains duplicate IDs")
            for pattern_id in pattern_ids:
                if pattern_id in seen_ids:
                    raise OutputValidationError(f"duplicate finding identity: {pattern_id}")
                seen_ids.add(pattern_id)

            count = finding.get("count")
            percentage = finding.get("percentage")
            is_unquantified = pattern_ids == ["unquantified"]
            if is_unquantified:
                if count is not None or percentage is not None:
                    raise OutputValidationError(
                        f"{path} unquantified findings must not claim count or percentage"
                    )
                continue
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise OutputValidationError(f"{path}.count must be a non-negative integer")
            if count > total:
                raise OutputValidationError(f"{path}.count cannot exceed total_entries")
            if not isinstance(percentage, (int, float)) or isinstance(percentage, bool):
                raise OutputValidationError(f"{path}.percentage must be a number")
            if not math.isfinite(float(percentage)) or not 0 <= float(percentage) <= 100:
                raise OutputValidationError(f"{path}.percentage must be finite and in [0, 100]")
            expected = round((count / total) * 100, 2) if total else 0.0
            if abs(float(percentage) - expected) > 0.01:
                raise OutputValidationError(
                    f"{path}.percentage is inconsistent with count / total "
                    f"({percentage} vs {expected})"
                )

    _validate_finding_detail_density(result, label=label)
    _validate_repetition(result, label=label)
    _validate_causation_language(result, label=label)


def validate_daily_report_result(result: dict, *, label: str = "daily_report output") -> None:
    """Validate the semantic-only Daily Report plan beyond JSON Schema.

    The model may supply only the bilingual general summary. Application-owned
    identifiers and storage coordinates must never leak into that narrative;
    composition resolves all report coverage and evidence deterministically.
    """
    if not isinstance(result, dict) or set(result) != {"general_summary"}:
        raise OutputValidationError(f"{label} must contain only general_summary")
    summary = result.get("general_summary")
    if not isinstance(summary, dict):
        raise OutputValidationError(f"{label}.general_summary must be an object")
    for language in ("zh", "en"):
        value = summary.get(language)
        if not isinstance(value, str) or not value.strip():
            raise OutputValidationError(f"{label}.general_summary.{language} must be non-empty")
        if len(value) > 1600:
            raise OutputValidationError(f"{label}.general_summary.{language} is too long")
        lowered = value.casefold()
        if any(marker in lowered for marker in ("object_key", "analysis_run_id", "bucket", "minio", "s3://")):
            raise OutputValidationError(
                f"{label}.general_summary.{language} contains application storage/provenance metadata"
            )
        if re.search(
            r"https?://|INC-\d+|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            value,
            re.I,
        ):
            raise OutputValidationError(
                f"{label}.general_summary.{language} contains an application identifier or URL"
            )


def validate_against_schema(output: dict, schema: dict, *, label: str = "output") -> None:
    """Generic JSON Schema validation, wrapping jsonschema's own exception
    in OutputValidationError so callers (and noc_bridge.failures'
    classify_failure) keep dealing with one exception type regardless of
    which skill or schema failed."""
    try:
        # jsonschema does not enforce annotations such as ``format: uuid``
        # unless a checker is supplied.  Job messages use that format for
        # identity fields, so every consumer-side validation must enforce it
        # at this seam rather than allowing a malformed ID to fail later in
        # the RabbitMQ callback.
        jsonschema.validate(
            instance=output,
            schema=schema,
            format_checker=jsonschema.FormatChecker(),
        )
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
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise OutputValidationError(f"no output schema found for skill {skill_name!r} at {schema_path}") from exc
    label = f"{job_type} output"
    validate_against_schema(output, schema, label=label)
    # Old immutable SkillSnapshots remain executable and their legacy output
    # includes cause/action fields. The active schema rejects those fields;
    # this branch keeps only compatibility execution/results from bypassing
    # the historical adapter while applying strict semantics to new output.
    if skill_name == "log-triage-summary" and not any(
        field in output
        for field in (
            "likely_cause_en", "likely_cause_zh",
            "recommended_action_en", "recommended_action_zh",
        )
    ):
        validate_log_triage_result(output, label=label)


def validate_merged_daily_report(output: dict) -> None:
    """Validates the final, deterministically-merged daily report
    structure (title/overview/sections, each carrying through screenshots/
    links/existing analysis verbatim) — the same contract
    bridge/noc_bridge/docx_render.py has always depended on. See this
    module's docstring for why this one stays hand-written rather than
    schema-driven."""
    from noc_bridge.report_validation import validate_merged_daily_report as _validate
    _validate(output)
