from __future__ import annotations

import base64
import pathlib
import zipfile

import pytest
from docx import Document

from noc_bridge.docx_render import render_document
from noc_bridge.report_composition import compose_report
from noc_bridge.validation import OutputValidationError, validate_output


ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _snapshot() -> dict:
    return {
        "shift_starts_at": "2026-09-14T00:00:00+00:00",
        "shift_ends_at": "2026-09-14T08:00:00+00:00",
        "report_skill_snapshot_id": "report-v2",
        "report_skill_execution_hash": "report-exec-v2",
        "incidents": [
            {
                "id": "inc-001", "display_id": "INC-001", "title": "Payment timeout",
                "status": "RECOVERED", "service": "payments", "environment": "prod",
                "triggered_at": "2026-09-14T01:00:00+00:00", "recovered_at": "2026-09-14T01:30:00+00:00",
                "trigger_value": "95%", "teams_url": "https://teams.example/inc-001",
                "grafana_url": "https://grafana.example/inc-001", "log_filename": "payments.log",
                "screenshots": [{"bucket": "noc-evidence", "object_key": "inc-001.png", "filename": "alert.png"}],
                "analysis_run_id": "run-001", "analysis_skill_snapshot_id": "triage-v1",
                "analysis_skill_execution_hash": "triage-exec-v1", "analysis_skill_version": "1",
                "analysis_output_sha256": "result-001", "analysis_model": "sonnet", "analysis_effort": "low",
                "report_fragment": {
                    "contract": "report-fragment-v1", "headline": "Payment timeout",
                    "severity": "high", "summary": {"zh": "支付超时", "en": "Payment timeout"},
                    "likely_cause": {"zh": "下游缓慢", "en": "Slow dependency"},
                    "recommended_action": {"zh": "检查依赖", "en": "Inspect dependency"},
                    "findings": [],
                },
            },
            {
                "id": "inc-002", "display_id": "INC-002", "title": "Cache errors",
                "status": "ACTIVE", "service": "cache", "environment": "prod",
                "triggered_at": "2026-09-14T02:00:00+00:00", "recovered_at": None,
                "trigger_value": None, "teams_url": None, "grafana_url": None, "log_filename": None,
                "screenshots": [], "analysis_run_id": "run-002", "analysis_skill_snapshot_id": "triage-v2",
                "analysis_skill_execution_hash": "triage-exec-v2", "analysis_skill_version": "2",
                "analysis_output_sha256": "result-002", "analysis_model": "sonnet", "analysis_effort": "medium",
                "report_fragment": {
                    "contract": "report-fragment-v1", "headline": "Cache errors",
                    "severity": "medium", "summary": {"zh": "缓存错误", "en": "Cache errors"},
                    "likely_cause": {"zh": "", "en": ""}, "recommended_action": {"zh": "", "en": ""},
                    "findings": [],
                },
            },
        ],
    }


def test_plan_resolves_frozen_facts_and_embeds_only_trusted_screenshot(tmp_path):
    plan = {
        "title": "Executive Overview",
        "blocks": [
            {"type": "heading", "text": "Critical Incidents", "level": 1},
            {"type": "incident_reference", "incident_id": "inc-001"},
            {"type": "analysis_reference", "analysis_run_id": "run-001"},
            {"type": "divider"},
            {"type": "bilingual_generated_text", "zh": "班次稳定。", "en": "The shift was stable."},
            {"type": "page_break"},
        ],
    }
    document = compose_report(plan, _snapshot())
    assert document.blocks[1].incident_id == "inc-001"
    assert {link.label for link in document.blocks[1].links} == {"Teams", "Grafana"}
    analysis = document.blocks[2]
    assert analysis.analysis_run_id == "run-001"
    assert any(item.label == "Execution Hash" for item in analysis.metadata)

    destination = tmp_path / "report.docx"
    render_document(document, destination, screenshot_fetcher=lambda bucket, key: _PNG)
    text = "\n".join(paragraph.text for paragraph in Document(str(destination)).paragraphs)
    assert "Trigger Value: 95%" in text
    assert "Payment timeout" in text
    with zipfile.ZipFile(destination) as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())


def test_unknown_references_are_rejected():
    with pytest.raises(OutputValidationError, match="unknown incident reference"):
        compose_report({"blocks": [{"type": "incident_reference", "incident_id": "secret"}]}, _snapshot())
    with pytest.raises(OutputValidationError, match="outside this report"):
        compose_report({"blocks": [{"type": "analysis_reference", "analysis_run_id": "run-999"}]}, _snapshot())


def test_storage_references_are_not_part_of_the_report_plan_contract():
    malicious = {
        "blocks": [{"type": "screenshot", "bucket": "other-bucket", "object_key": "secret/file"}]
    }
    with pytest.raises(Exception):
        validate_output("daily_report", malicious, skill_name="daily-alert-report", skills_dir=SKILLS_DIR)
