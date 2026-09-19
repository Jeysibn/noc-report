"""Structured output validation (master plan §27 step 13). Validates the
sandbox's `result.json` against the contract the invoked skill promises,
before it's ever uploaded to MinIO or treated as a real result — a bad or
truncated model/stand-in output must fail loudly here, not surface as a
malformed AnalysisRun/Report downstream.

Skill Runtime validation is generic: it loads the selected skill's own
`output.schema.json` from the same per-job materialized directory used by the
sandbox and runs standard JSON Schema validation. A skill's output format can
change by editing its schema file alone; generic bridge code does not define
result fields.

The historical `daily_report_docx` compatibility assembler has its own
validation in `report_validation.py`. Generic runtime validation does not
inspect report fields or semantics; current `report-document-v1` reports use
the Daily Alert Report skill's narrative-only schema.
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
    validate_against_schema(output, schema, label=f"{job_type} output")


def validate_merged_daily_report(output: dict) -> None:
    """Validates the final, deterministically-merged daily report
    structure (title/overview/sections, each carrying through screenshots/
    links/existing analysis verbatim) — the same contract
    bridge/noc_bridge/docx_render.py has always depended on. See this
    module's docstring for why this one stays hand-written rather than
    schema-driven."""
    from noc_bridge.report_validation import validate_merged_daily_report as _validate
    _validate(output)
