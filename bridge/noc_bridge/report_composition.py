"""Deterministic ReportPlan -> ReportDocument composition.

The Daily Report skill owns narrative reasoning. This module owns canonical
section order and every trusted fact lookup: incident/evidence resolution,
analysis provenance, links, filenames, and storage references. A model-
generated plan therefore cannot authorize a MinIO read, omit a mandatory
incident, or invent an AnalysisRun.
"""
from __future__ import annotations

from noc_bridge.report_document import (
    AnalysisReference,
    BilingualText,
    Divider,
    Heading,
    IncidentEvidence,
    Link,
    LogFileReference,
    Metadata,
    PageBreak,
    Paragraph,
    ReportDocument,
    Screenshot,
    screenshot_from_dict,
)
from noc_bridge.analysis_presentation import build_analysis_presentation
from noc_bridge.time_projection import project_shift_time
from noc_bridge.validation import OutputValidationError

DAILY_REPORT_COMPOSITION_PROFILE = "noc-daily-report-v1"
_CANONICAL_TOP_LEVEL_HEADINGS = {"Alerts", "General Summary", "Log Analysis"}


def _legacy_fragment(result: dict | None) -> dict | None:
    """Compatibility adapter for snapshots frozen before report_fragment.

    This is deliberately isolated from the active composition contract and
    is only used when an old immutable snapshot is rendered.
    """
    if not isinstance(result, dict):
        return None
    return {
        "contract": "report-fragment-v1",
        "headline": str(result.get("summary_en") or result.get("summary_zh") or ""),
        "severity": result.get("severity_signal"),
        "summary": {"zh": str(result.get("summary_zh") or ""), "en": str(result.get("summary_en") or "")},
        "likely_cause": {"zh": str(result.get("likely_cause_zh") or result.get("likely_cause") or ""), "en": str(result.get("likely_cause_en") or result.get("likely_cause") or "")},
        "recommended_action": {"zh": str(result.get("recommended_action_zh") or result.get("recommended_action") or ""), "en": str(result.get("recommended_action_en") or result.get("recommended_action") or "")},
        "findings": [
            {
                "zh": str(item.get("label_zh") or item.get("label_en") or ""),
                "en": str(item.get("label_en") or item.get("label_zh") or ""),
                "detail_zh": str(item.get("detail_zh") or ""),
                "detail_en": str(item.get("detail_en") or ""),
            }
            for item in (result.get("key_finds") or [])[:5]
            if isinstance(item, dict)
        ],
    }


def _pair(fragment: dict, key: str) -> BilingualText | None:
    value = fragment.get(key) or {}
    if not isinstance(value, dict):
        return None
    zh, en = str(value.get("zh") or ""), str(value.get("en") or "")
    if not zh and not en:
        return None
    return BilingualText(zh, en, heading_zh="Chinese", heading_en="English")


def _metadata(incident: dict) -> tuple[Metadata, ...]:
    values = [
        ("Incident", incident.get("display_id") or incident.get("id")),
        ("Status", incident.get("status")),
        ("Service", incident.get("service")),
        ("Environment", incident.get("environment")),
        ("Triggered", incident.get("triggered_at")),
        ("Recovered", incident.get("recovered_at")),
        ("Trigger Value", incident.get("trigger_value")),
    ]
    return tuple(Metadata(label, str(value)) for label, value in values if value not in (None, ""))


def _incident_block(incident: dict, number: int, *, canonical: bool = False) -> IncidentEvidence:
    links = []
    if not canonical and incident.get("teams_url"):
        links.append(Link("Teams", incident["teams_url"], text="Teams", prefix="Teams Link"))
    if incident.get("grafana_url"):
        links.append(Link("Grafana", incident["grafana_url"], text="Grafana", prefix="Grafana Link"))
    screenshots = tuple(
        screenshot_from_dict(item)
        for item in incident.get("screenshots") or []
        if isinstance(item, dict) and item.get("bucket") and item.get("object_key")
    )
    return IncidentEvidence(
        heading=f"Alert #{number} - {incident.get('title') or incident.get('display_id') or 'Incident'}",
        incident_id=str(incident["id"]),
        # The canonical operator report exposes only operational evidence.
        # Incident metadata remains in the frozen snapshot for audit views.
        metadata=() if canonical else _metadata(incident),
        links=tuple(links),
        log_file=(
            LogFileReference(incident["log_filename"], incident.get("log_file_url"))
            if incident.get("log_filename")
            else None
        ),
        screenshots=screenshots,
    )


def _analysis_block(incident: dict, plan_node: dict | None, number: int) -> AnalysisReference:
    requested_run_id = plan_node.get("analysis_run_id") if plan_node else None
    requested_incident_id = plan_node.get("incident_id") if plan_node else None
    if plan_node is not None and requested_run_id is None and requested_incident_id is None:
        raise OutputValidationError("analysis_reference requires analysis_run_id or incident_id")
    if requested_incident_id is not None and str(requested_incident_id) not in {
        str(incident.get("id")), str(incident.get("display_id"))
    }:
        raise OutputValidationError("analysis_reference incident_id does not match analysis run")
    available = bool(incident.get("analysis_run_id"))
    if requested_run_id is not None and str(requested_run_id) != str(incident.get("analysis_run_id")):
        raise OutputValidationError(
            f"analysis reference {requested_run_id!r} is not the frozen run for incident {incident['id']}"
        )
    run_id = incident.get("analysis_run_id")
    if not available:
        return AnalysisReference(
            heading=f"Alert #{number} - {incident.get('title') or incident.get('display_id') or 'Analysis'}",
            incident_id=str(incident["id"]),
            available=False,
            analysis_run_id=None,
            unavailable_text="No log analysis available for this incident.",
        )

    analysis = incident.get("analysis")
    fragment = incident.get("report_fragment") or _legacy_fragment(analysis)
    if not isinstance(fragment, dict) and not isinstance(analysis, dict):
        return AnalysisReference(
            heading=f"Alert #{number} - {incident.get('title') or incident.get('display_id') or 'Analysis'}",
            incident_id=str(incident["id"]),
            available=False,
            analysis_run_id=str(run_id),
            unavailable_text="The frozen analysis has no report export.",
        )
    try:
        children = list(build_analysis_presentation(
            analysis,
            incident.get("analysis_presentation_contract"),
        ))
    except ValueError as exc:
        raise OutputValidationError(str(exc)) from exc
    if not children:
        # Old immutable snapshots may contain only the compact export. Keep
        # those historical reports renderable while new snapshots use the
        # complete stored AnalysisRun result above.
        summary = _pair(fragment, "summary")
        if summary:
            children.append(summary)
        for label, key in (("Likely Cause", "likely_cause"), ("Recommended Action", "recommended_action")):
            value = _pair(fragment, key)
            if value:
                children.extend((Heading(label, level=3), value))
    children.insert(0, Heading("Log Analysis", level=3))
    provenance = tuple(
        Metadata(label, str(value))
        for label, value in (
            ("Skill Snapshot", incident.get("analysis_skill_snapshot_id")),
            ("Execution Hash", incident.get("analysis_skill_execution_hash")),
            ("Skill Version", incident.get("analysis_skill_version")),
            ("Result Hash", incident.get("analysis_output_sha256")),
            ("Model", incident.get("analysis_model")),
            ("Effort", incident.get("analysis_effort")),
        )
        if value not in (None, "")
    )
    return AnalysisReference(
        heading=f"Alert #{number} - {incident.get('title') or incident.get('display_id') or 'Analysis'}",
        available=True,
        incident_id=str(incident["id"]),
        analysis_run_id=str(run_id),
        provenance=provenance,
        screenshots=tuple(
            screenshot_from_dict(item)
            for item in (incident.get("screenshots") or [])
            if isinstance(item, dict) and item.get("bucket") and item.get("object_key")
        ),
        log_file=(
            LogFileReference(incident["log_filename"], incident.get("log_file_url"))
            if incident.get("log_filename")
            else None
        ),
        children=tuple(children),
    )


def compose_report(plan: dict, snapshot: dict) -> ReportDocument:
    """Resolve one validated ReportPlan against one frozen snapshot."""
    if not isinstance(plan, dict):
        raise OutputValidationError("ReportPlan must be an object")
    coverage = snapshot.get("coverage") or {}
    if not isinstance(coverage, dict):
        raise OutputValidationError("report coverage policy must be an object")
    composition_profile = snapshot.get("composition_profile") or coverage.get("composition_profile")
    if composition_profile not in (None, DAILY_REPORT_COMPOSITION_PROFILE):
        raise OutputValidationError(f"unsupported report composition profile: {composition_profile}")
    canonical = composition_profile == DAILY_REPORT_COMPOSITION_PROFILE
    incident_policy = coverage.get("incidents", "optional")
    analysis_policy = coverage.get("analyses", "optional")
    allow_duplicate_incidents = bool(coverage.get("allow_duplicate_incidents", False))
    allow_duplicate_analyses = bool(coverage.get("allow_duplicate_analyses", False))

    incidents: dict[str, dict] = {}
    unique_incidents: list[dict] = []
    for incident in snapshot.get("incidents") or []:
        if not isinstance(incident, dict) or not incident.get("id"):
            raise OutputValidationError("frozen report incident is missing an id")
        incident_id = str(incident["id"])
        if incident_id in incidents:
            raise OutputValidationError(f"duplicate incident in frozen report snapshot: {incident_id}")
        incidents[incident_id] = incident
        unique_incidents.append(incident)
        if incident.get("display_id"):
            display_id = str(incident["display_id"])
            if display_id in incidents:
                raise OutputValidationError(f"duplicate incident display_id in frozen report snapshot: {display_id}")
            incidents[display_id] = incident

    incident_refs: list[str] = []
    analysis_refs: list[str] = []
    analysis_plan_nodes: dict[str, dict] = {}

    def required_text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise OutputValidationError(f"{label} must be a non-empty string")
        return value

    def narrative_summary(value: object, label: str) -> BilingualText:
        if not isinstance(value, dict):
            raise OutputValidationError(f"{label} must contain zh and en")
        return BilingualText(
            required_text(value.get("zh"), f"{label}.zh"),
            required_text(value.get("en"), f"{label}.en"),
            "Chinese Summary",
            "English Summary",
        )

    def parse(nodes: list[dict]) -> list:
        """Validate references while collecting only model-authored prose.

        Incident and analysis nodes are authorization requests, not layout
        instructions. Their final position and numbering are materialized
        from the frozen snapshot below.
        """
        summary: list = []
        for node in nodes:
            kind = node.get("type") if isinstance(node, dict) else None
            if kind == "heading":
                text = str(node["text"])
                if text in _CANONICAL_TOP_LEVEL_HEADINGS and int(node.get("level", 1)) == 1:
                    continue
                summary.append(Heading(text, int(node.get("level", 1))))
            elif kind == "paragraph":
                summary.append(Paragraph(str(node.get("text") or ""), node.get("style")))
            elif kind == "bilingual_generated_text":
                summary.append(BilingualText(
                    str(node.get("zh") or ""), str(node.get("en") or ""),
                    node.get("heading_zh") or "Chinese Summary",
                    node.get("heading_en") or "English Summary",
                ))
            elif kind == "incident_reference":
                ref = str(node.get("incident_id") or "")
                incident = incidents.get(ref)
                if incident is None:
                    raise OutputValidationError(f"unknown incident reference: {ref}")
                incident_id = str(incident["id"])
                incident_refs.append(incident_id)
            elif kind == "analysis_reference":
                ref = str(node.get("incident_id") or "")
                if ref:
                    incident = incidents.get(ref)
                    if incident is None:
                        raise OutputValidationError(f"unknown incident reference: {ref}")
                else:
                    run_id = str(node.get("analysis_run_id") or "")
                    incident = next(
                        (item for item in snapshot.get("incidents") or []
                         if str(item.get("analysis_run_id")) == run_id),
                        None,
                    )
                    if incident is None:
                        raise OutputValidationError(f"analysis reference is outside this report: {run_id}")
                incident_id = str(incident["id"])
                if incident.get("analysis_run_id"):
                    analysis_refs.append(str(incident["analysis_run_id"]))
                analysis_plan_nodes[incident_id] = node
            elif kind == "divider":
                summary.append(Divider())
            elif kind == "page_break":
                # Canonical section breaks are inserted deterministically
                # below; a plan-authored page break inside a section is
                # otherwise redundant, so it is dropped rather than trusted
                # to land in the right place.
                continue
            else:
                raise OutputValidationError(f"unsupported ReportPlan node type: {kind!r}")
        return summary

    narrative_mode = "general_summary" in plan
    if canonical and not narrative_mode:
        raise OutputValidationError("canonical Daily Report requires general_summary")
    if narrative_mode:
        summary_blocks = [narrative_summary(plan.get("general_summary"), "general_summary")]
        if plan.get("cross_incident_findings") is not None:
            summary_blocks.extend((Heading("Cross-Incident Findings", level=2), narrative_summary(
                plan.get("cross_incident_findings"), "cross_incident_findings"
            )))
    else:
        if not isinstance(plan.get("blocks"), list):
            raise OutputValidationError("ReportPlan must contain a blocks array")
        summary_blocks = parse(plan["blocks"])

    if not narrative_mode and not allow_duplicate_incidents and len(incident_refs) != len(set(incident_refs)):
        raise OutputValidationError("duplicate incident reference in ReportPlan")
    if not narrative_mode and not allow_duplicate_analyses and len(analysis_refs) != len(set(analysis_refs)):
        raise OutputValidationError("duplicate analysis reference in ReportPlan")
    if incident_policy not in ("all", "none", "optional"):
        raise OutputValidationError(f"unsupported incident coverage policy: {incident_policy}")
    if analysis_policy not in ("all_available", "none", "optional"):
        raise OutputValidationError(f"unsupported analysis coverage policy: {analysis_policy}")
    if not narrative_mode and incident_policy == "all":
        required = {str(item["id"]) for item in unique_incidents}
        missing = required - set(incident_refs)
        if missing:
            raise OutputValidationError(f"ReportPlan omitted required incidents: {sorted(missing)}")
    if not narrative_mode and analysis_policy == "all_available":
        required = {str(item["analysis_run_id"]) for item in unique_incidents if item.get("analysis_run_id")}
        missing = required - set(analysis_refs)
        if missing:
            raise OutputValidationError(f"ReportPlan omitted required analyses: {sorted(missing)}")

    # Canonical profiles number and order from the frozen report, never from
    # Claude's arbitrary reference order. Optional legacy profiles retain the
    # plan's selected order for historical snapshots.
    if incident_policy == "all":
        alert_incidents = unique_incidents
    else:
        alert_incidents = []
        seen: set[str] = set()
        for incident_id in incident_refs:
            if incident_id not in seen:
                alert_incidents.append(incidents[incident_id])
                seen.add(incident_id)

    alert_number_by_id = {
        str(incident["id"]): number
        for number, incident in enumerate(alert_incidents, start=1)
    }

    if analysis_policy == "all_available":
        analysis_incidents = [item for item in unique_incidents if item.get("analysis_run_id")]
    else:
        analysis_incidents = [incidents[incident_id] for incident_id in analysis_plan_nodes]

    parsed_blocks: list = []
    parsed_blocks.append(Heading("Alerts", level=1))
    parsed_blocks.extend(_incident_block(incident, i, canonical=canonical) for i, incident in enumerate(alert_incidents, start=1))
    parsed_blocks.append(PageBreak())
    parsed_blocks.append(Heading("General Summary", level=1))
    parsed_blocks.extend(summary_blocks)
    parsed_blocks.append(PageBreak())
    parsed_blocks.append(Heading("Log Analysis", level=1))
    parsed_blocks.extend(
        _analysis_block(
            incident,
            analysis_plan_nodes.get(str(incident["id"])) if narrative_mode else analysis_plan_nodes.get(str(incident["id"]), {"incident_id": str(incident["id"])}),
            alert_number_by_id.get(str(incident["id"]), i),
        )
        for i, incident in enumerate(analysis_incidents, start=1)
    )

    try:
        display_date = project_shift_time(snapshot).report_date
    except ValueError as exc:
        raise OutputValidationError(str(exc)) from exc
    shift_display = snapshot.get("shift_display_name") or snapshot.get("shift_code") or snapshot.get("shift_name") or "Shift"
    metadata = tuple(Metadata(label, str(value)) for label, value in (("Date", display_date), ("Shift", shift_display)))
    provenance = tuple(
        Metadata(label, str(value))
        for label, value in (
            ("Report Skill Snapshot", snapshot.get("report_skill_snapshot_id")),
            ("Report Execution Hash", snapshot.get("report_skill_execution_hash")),
        )
        if value not in (None, "")
    )
    return ReportDocument(
        title="Daily Alert & Log Analysis Report",
        blocks=tuple(parsed_blocks),
        metadata=metadata,
        provenance=provenance,
    )
