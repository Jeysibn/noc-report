"""Deterministic ReportPlan -> ReportDocument composition.

The Daily Report skill owns ordering and narrative.  This module owns every
trusted fact lookup: incident/evidence resolution, analysis provenance,
links, filenames, and storage references.  A model-generated plan therefore
cannot authorize a MinIO read or invent an AnalysisRun.
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
)
from noc_bridge.analysis_presentation import build_analysis_presentation
from noc_bridge.validation import OutputValidationError


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


def _incident_block(incident: dict) -> IncidentEvidence:
    links = []
    if incident.get("teams_url"):
        links.append(Link("Teams", incident["teams_url"]))
    if incident.get("grafana_url"):
        links.append(Link("Grafana", incident["grafana_url"]))
    screenshots = tuple(
        Screenshot(item["bucket"], item["object_key"], item.get("filename"))
        for item in incident.get("screenshots") or []
        if isinstance(item, dict) and item.get("bucket") and item.get("object_key")
    )
    return IncidentEvidence(
        heading=f"Alert — {incident.get('title') or incident.get('display_id') or 'Incident'}",
        incident_id=str(incident["id"]),
        metadata=_metadata(incident),
        links=tuple(links),
        log_file=LogFileReference(incident["log_filename"]) if incident.get("log_filename") else None,
        screenshots=screenshots,
    )


def _analysis_block(incident: dict, plan_node: dict) -> AnalysisReference:
    requested_run_id = plan_node.get("analysis_run_id")
    requested_incident_id = plan_node.get("incident_id")
    if requested_run_id is None and requested_incident_id is None:
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
            heading=f"{incident.get('display_id') or incident['id']} — {incident.get('title') or 'Analysis'}",
            available=False,
            analysis_run_id=None,
            unavailable_text="No log analysis available for this incident.",
        )

    analysis = incident.get("analysis")
    fragment = incident.get("report_fragment") or _legacy_fragment(analysis)
    if not isinstance(fragment, dict) and not isinstance(analysis, dict):
        return AnalysisReference(
            heading=f"{incident.get('display_id') or incident['id']} — {incident.get('title') or 'Analysis'}",
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
        heading=f"{incident.get('display_id') or incident['id']} — {incident.get('title') or 'Analysis'}",
        available=True,
        analysis_run_id=str(run_id),
        metadata=provenance,
        children=tuple(children),
    )


def compose_report(plan: dict, snapshot: dict) -> ReportDocument:
    """Resolve one validated ReportPlan against one frozen snapshot."""
    if not isinstance(plan, dict) or not isinstance(plan.get("blocks"), list):
        raise OutputValidationError("ReportPlan must contain a blocks array")
    coverage = snapshot.get("coverage") or {}
    if not isinstance(coverage, dict):
        raise OutputValidationError("report coverage policy must be an object")
    incident_policy = coverage.get("incidents", "optional")
    analysis_policy = coverage.get("analyses", "optional")
    allow_duplicate_incidents = bool(coverage.get("allow_duplicate_incidents", False))
    allow_duplicate_analyses = bool(coverage.get("allow_duplicate_analyses", False))

    incidents = {}
    unique_incidents = []
    for incident in snapshot.get("incidents") or []:
        if not isinstance(incident, dict) or not incident.get("id"):
            continue
        incidents[str(incident["id"])] = incident
        unique_incidents.append(incident)
        if incident.get("display_id"):
            incidents[str(incident["display_id"])] = incident

    incident_refs: list[str] = []
    analysis_refs: list[str] = []

    def parse(nodes: list[dict]):
        blocks = []
        for node in nodes:
            kind = node.get("type") if isinstance(node, dict) else None
            if kind == "heading":
                blocks.append(Heading(str(node["text"]), int(node.get("level", 1))))
            elif kind == "paragraph":
                blocks.append(Paragraph(str(node.get("text") or ""), node.get("style")))
            elif kind == "bilingual_generated_text":
                blocks.append(BilingualText(
                    str(node.get("zh") or ""), str(node.get("en") or ""),
                    node.get("heading_zh"), node.get("heading_en"),
                ))
            elif kind == "incident_reference":
                ref = str(node.get("incident_id") or "")
                incident = incidents.get(ref)
                if incident is None:
                    raise OutputValidationError(f"unknown incident reference: {ref}")
                incident_refs.append(str(incident["id"]))
                blocks.append(_incident_block(incident))
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
                blocks.append(_analysis_block(incident, node))
                if incident.get("analysis_run_id"):
                    analysis_refs.append(str(incident["analysis_run_id"]))
            elif kind == "divider":
                blocks.append(Divider())
            elif kind == "page_break":
                blocks.append(PageBreak())
            else:
                raise OutputValidationError(f"unsupported ReportPlan node type: {kind!r}")
        return blocks

    parsed_blocks = parse(plan["blocks"])

    if not allow_duplicate_incidents and len(incident_refs) != len(set(incident_refs)):
        raise OutputValidationError("duplicate incident reference in ReportPlan")
    if not allow_duplicate_analyses and len(analysis_refs) != len(set(analysis_refs)):
        raise OutputValidationError("duplicate analysis reference in ReportPlan")
    if incident_policy == "all":
        required = {str(item["id"]) for item in unique_incidents}
        missing = required - set(incident_refs)
        if missing:
            raise OutputValidationError(f"ReportPlan omitted required incidents: {sorted(missing)}")
    elif incident_policy not in ("none", "optional"):
        raise OutputValidationError(f"unsupported incident coverage policy: {incident_policy}")
    if analysis_policy == "all_available":
        required = {str(item["analysis_run_id"]) for item in unique_incidents if item.get("analysis_run_id")}
        missing = required - set(analysis_refs)
        if missing:
            raise OutputValidationError(f"ReportPlan omitted required analyses: {sorted(missing)}")
    elif analysis_policy not in ("none", "optional"):
        raise OutputValidationError(f"unsupported analysis coverage policy: {analysis_policy}")

    starts = snapshot.get("shift_starts_at") or ""
    ends = snapshot.get("shift_ends_at") or "ongoing"
    title = str(plan.get("title") or f"Daily Alert Report — {starts} to {ends}")
    metadata = (
        Metadata("Date", str(starts).split("T", 1)[0]),
        Metadata("Shift", f"{starts} to {ends}"),
        Metadata("Skill Snapshot", str(snapshot.get("report_skill_snapshot_id") or "")),
        Metadata("Execution Hash", str(snapshot.get("report_skill_execution_hash") or "")),
    )
    return ReportDocument(title=title, blocks=tuple(parsed_blocks), metadata=metadata)
