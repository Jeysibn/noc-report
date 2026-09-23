"""Provider-neutral Daily Alert Report context and artifact helpers.

The report snapshot is application truth. This module exposes only the
skill-declared compact semantic projection to Hermes and keeps the final
ReportDocument/DOCX composition deterministic in runtime support.
"""
from __future__ import annotations

import json
import pathlib
from typing import Callable

from noc_bridge.report_composition import compose_report
from noc_bridge.report_document_json import document_to_preview_json
from noc_bridge.storage import read_artifact_metadata


DAILY_REPORT_CONTEXT_VERSION = "daily-report-context-v1"
REPORT_PLAN_OBJECT_KEY = "jobs/{job_id}/report-plan.json"
REPORT_TELEMETRY_OBJECT_KEY = "jobs/{job_id}/report-telemetry.json"
REPORT_DOCX_OBJECT_KEY = "reports/{job_id}/report.docx"
REPORT_DOCUMENT_OBJECT_KEY = "reports/{job_id}/document.json"
REPORT_SCREENSHOTS_OBJECT_KEY = "reports/{job_id}/document.screenshots.json"


def _compact_fragment(fragment: object) -> dict | None:
    if not isinstance(fragment, dict):
        return None
    allowed = {"contract", "headline", "severity", "summary", "findings", "likely_cause", "recommended_action"}
    result = {key: fragment[key] for key in allowed if key in fragment}
    findings = result.get("findings")
    if isinstance(findings, list):
        result["findings"] = [item for item in findings[:5] if isinstance(item, dict)]
    return result


def build_daily_report_input(snapshot: dict) -> dict:
    """Project a frozen ReportSnapshot into the daily-report skill contract.

    Storage coordinates, screenshots, URLs, raw analysis, and provenance stay
    application-side. Titles and notes are still explicitly untrusted data in
    the Hermes system boundary.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("report snapshot must be an object")
    incidents = []
    for incident_number, incident in enumerate(snapshot.get("incidents") or [], start=1):
        if not isinstance(incident, dict):
            continue
        incidents.append(
            {
                # Application identifiers and exact timestamps are deliberately
                # not part of the semantic projection. Report composition uses
                # the frozen snapshot for those trusted presentation facts.
                "incident_number": incident_number,
                "title": incident.get("title"),
                "status": incident.get("status"),
                "service": incident.get("service"),
                "environment": incident.get("environment"),
                "report_fragment": _compact_fragment(incident.get("report_fragment")),
            }
        )
    return {
        "schema_version": DAILY_REPORT_CONTEXT_VERSION,
        "task": "daily_report",
        "language_order": ["zh-CN", "en"],
        "shift": {
            "name": snapshot.get("shift_display_name") or snapshot.get("shift_name"),
            "timezone": snapshot.get("shift_timezone") or "UTC",
        },
        "incidents": incidents,
    }


def report_object_keys(job_id: str) -> dict[str, str]:
    return {
        "plan": REPORT_PLAN_OBJECT_KEY.format(job_id=job_id),
        "telemetry": REPORT_TELEMETRY_OBJECT_KEY.format(job_id=job_id),
        "report": REPORT_DOCX_OBJECT_KEY.format(job_id=job_id),
        "document": REPORT_DOCUMENT_OBJECT_KEY.format(job_id=job_id),
        "screenshots": REPORT_SCREENSHOTS_OBJECT_KEY.format(job_id=job_id),
    }


def load_saved_plan(client, *, bucket: str, job_id: str) -> dict | None:
    key = report_object_keys(job_id)["plan"]
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        value = json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def existing_report_artifacts(client, *, bucket: str, job_id: str) -> dict | None:
    """Return immutable metadata for a complete renderer artifact set."""
    keys = report_object_keys(job_id)
    try:
        return {
            name: read_artifact_metadata(client, bucket=bucket, object_key=keys[name])
            for name in ("report", "document", "screenshots")
        }
    except Exception:
        return None


def compose_and_render(
    plan: dict,
    snapshot: dict,
    *,
    docx_path: pathlib.Path,
    screenshot_fetcher: Callable,
) -> tuple[dict, list[dict]]:
    """Compose one trusted ReportDocument and render the same document to DOCX."""
    document = compose_report(plan, snapshot)
    from noc_bridge.docx_render import render_document

    render_document(document, docx_path, screenshot_fetcher=screenshot_fetcher)
    preview, screenshots = document_to_preview_json(document)
    return preview, screenshots
