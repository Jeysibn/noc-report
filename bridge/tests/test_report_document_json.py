from __future__ import annotations

from noc_bridge.report_composition import compose_report
from noc_bridge.report_document import DOCUMENT_BLOCK_TYPES, build_report_document
from noc_bridge.report_document_json import document_to_preview_json


def _snapshot() -> dict:
    return {
        "shift_starts_at": "2026-09-13T00:00:00+00:00",
        "shift_ends_at": "2026-09-13T08:00:00+00:00",
        "report_skill_snapshot_id": "report-v2",
        "report_skill_execution_hash": "report-exec-v2",
        "incidents": [
            {
                "id": "inc-001", "display_id": "INC-001", "title": "Payment timeout",
                "status": "RECOVERED", "service": "payments", "environment": "prod",
                "triggered_at": "2026-09-13T01:00:00+00:00", "recovered_at": "2026-09-13T01:30:00+00:00",
                "trigger_value": "95%", "teams_url": None,
                "grafana_url": "https://grafana.example/inc-001",
                "log_filename": "1-payments-logs-2026-09-13.json",
                "screenshots": [{"bucket": "noc-evidence", "object_key": "inc-001.png", "filename": "alert.png"}],
                "analysis_run_id": "run-001", "analysis_skill_snapshot_id": "triage-v1",
                "analysis_skill_execution_hash": "triage-exec-v1", "analysis_skill_version": "1",
                "analysis_output_sha256": "result-001", "analysis_model": "sonnet", "analysis_effort": "low",
                "report_fragment": {
                    "contract": "report-fragment-v1", "headline": "Payment timeout",
                    "severity": "high", "summary": {"zh": "支付超时", "en": "Payment timeout"},
                    "likely_cause": {"zh": "", "en": ""}, "recommended_action": {"zh": "", "en": ""},
                    "findings": [],
                },
                "analysis": {
                    "summary_zh": "完整摘要", "summary_en": "Full summary",
                    "key_finds": [
                        {"label_zh": "重点1", "label_en": "Finding 1", "count": 10, "percentage": 50.0,
                         "detail_zh": "细节1", "detail_en": "Detail 1"},
                    ],
                    "secondary_finds": [],
                },
            },
        ],
    }


def test_screenshot_blocks_are_referenced_by_index_never_by_bucket_or_key():
    plan = {"blocks": [
        {"type": "incident_reference", "incident_id": "inc-001"},
        {"type": "bilingual_generated_text", "zh": "班次稳定。", "en": "The shift was stable."},
        {"type": "analysis_reference", "incident_id": "inc-001"},
    ]}
    document = compose_report(plan, _snapshot())
    payload, screenshot_index = document_to_preview_json(document)

    dumped = str(payload)
    assert "noc-evidence" not in dumped
    assert "inc-001.png" not in dumped  # object_key never leaks into the browser payload

    assert screenshot_index == [
        {"bucket": "noc-evidence", "object_key": "inc-001.png", "filename": "alert.png"},
        {"bucket": "noc-evidence", "object_key": "inc-001.png", "filename": "alert.png"},
    ]
    assert "analysis_run_id" not in str(payload)
    assert "Execution Hash" not in str(payload)

    def _find_screenshot_block(blocks):
        for block in blocks:
            if block.get("type") == "screenshot":
                return block
            for key in ("metadata", "links", "screenshots", "children"):
                if key in block and isinstance(block[key], list):
                    found = _find_screenshot_block(block[key])
                    if found:
                        return found
        return None

    shot = _find_screenshot_block(payload["blocks"])
    assert shot == {"type": "screenshot", "index": 0, "filename": "alert.png"}


def test_preview_json_mirrors_canonical_section_order():
    plan = {"blocks": [
        {"type": "incident_reference", "incident_id": "inc-001"},
        {"type": "bilingual_generated_text", "zh": "班次稳定。", "en": "The shift was stable."},
        {"type": "analysis_reference", "incident_id": "inc-001"},
    ]}
    document = compose_report(plan, _snapshot())
    payload, _ = document_to_preview_json(document)

    section_headings = [
        b["text"] for b in payload["blocks"] if b.get("type") == "heading" and b.get("level") == 1
    ]
    assert [h for h in section_headings if h in ("Alerts", "General Summary", "Log Analysis")] == [
        "Alerts", "General Summary", "Log Analysis"
    ]

    types_in_order = [b["type"] for b in payload["blocks"]]
    assert types_in_order.index("incident_evidence") < types_in_order.index("page_break")
    assert types_in_order.count("page_break") == 2
    last_incident_idx = max(i for i, t in enumerate(types_in_order) if t == "incident_evidence")
    first_analysis_idx = min(i for i, t in enumerate(types_in_order) if t == "analysis_reference")
    assert last_incident_idx < first_analysis_idx


def test_new_canonical_preview_contains_no_cross_incident_findings():
    document = compose_report(
        {
            "general_summary": {"zh": "摘要", "en": "Summary"},
            # A stale caller must not be able to reintroduce the removed
            # section into the deterministic active composition.
            "cross_incident_findings": {"zh": "旧", "en": "Removed"},
        },
        {**_snapshot(), "composition_profile": "noc-daily-report-v1", "coverage": {"incidents": "all", "analyses": "all_available"}},
    )
    payload, _ = document_to_preview_json(document)
    assert "Cross-Incident Findings" not in str(payload)
    assert "cross_incident_findings" not in str(payload)


def test_preview_json_serializes_every_documented_block_type():
    raw_blocks = [
        {"type": "heading", "text": "H", "level": 1},
        {"type": "paragraph", "text": "P"},
        {"type": "divider"},
        {"type": "page_break"},
        {"type": "metadata", "label": "M", "value": "V"},
        {"type": "link", "label": "Grafana", "url": "https://example.test", "text": "Grafana"},
        {"type": "log_file_reference", "filename": "log.json"},
        {"type": "screenshot", "bucket": "b", "object_key": "k", "filename": "s.png"},
        {"type": "alert_navigation", "entries": [{"text": "Alert", "target": "log_analysis_deadbeef"}]},
        {"type": "bilingual_text", "zh": "中", "en": "En"},
        {"type": "bilingual_find_list", "heading_zh": "重点", "heading_en": "Findings", "finds_zh": [], "finds_en": []},
        {"type": "find_list", "heading": "Key Finds", "language": "Chinese", "finds": []},
        {"type": "incident_evidence", "heading": "Alert", "metadata": [], "links": [], "screenshots": []},
        {"type": "analysis_reference", "heading": "Analysis", "available": True, "children": []},
    ]
    document = build_report_document({"metadata": {"title": "Contract"}, "blocks": raw_blocks})
    payload, _ = document_to_preview_json(document)
    assert {block["type"] for block in payload["blocks"]} == set(DOCUMENT_BLOCK_TYPES)
