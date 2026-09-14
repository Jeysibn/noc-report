"""AI cost-optimization mission Phase 2, Issue 6: regression tests proving
Claude only ever receives the compact per-incident summary for the
daily-alert-report skill — never the full snapshot (per-incident analysis
object, screenshots, MinIO/Grafana references)."""
from __future__ import annotations

import importlib.util
import json
import pathlib

_ENTRYPOINT_PATH = pathlib.Path(__file__).resolve().parents[1] / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("sandbox_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(entrypoint)


_SNAPSHOT = {
    "shift_starts_at": "2026-09-13T00:00:00+00:00",
    "shift_ends_at": "2026-09-13T08:00:00+00:00",
    "incidents": [
        {
            "display_id": "INC-001",
            "title": "Payment service errors",
            "status": "RESOLVED",
            "grafana_url": "https://grafana.example/d/abc",
            "log_filename": "payments.log",
            "screenshots": [{"bucket": "noc-evidence", "object_key": "k1", "filename": "s1.png"}],
            "triggered_at": "2026-09-13T01:00:00+00:00",
            "recovered_at": "2026-09-13T01:30:00+00:00",
            "analysis": {
                "summary_en": "NullPointerException in PaymentWorker.charge",
                "summary_zh": "支付服务空指针异常",
                "key_finds": [{"label_en": "NullPointerException", "count": 5}],
                "secondary_finds": [],
                "likely_cause_en": "null pointer dereference",
                "likely_cause_zh": "空指针解引用",
                "recommended_action_en": "patch", "recommended_action_zh": "修复",
                "severity_signal": "high",
                "confidence": 0.8,
            },
            "report_fragment": {
                "contract": "report-fragment-v1",
                "headline": "NullPointerException in PaymentWorker.charge",
                "severity": "high",
                "summary": {"zh": "支付服务空指针异常", "en": "NullPointerException in PaymentWorker.charge"},
                "likely_cause": {"zh": "空指针解引用", "en": "null pointer dereference"},
                "recommended_action": {"zh": "修复", "en": "patch"},
                "findings": [],
            },
        },
        {
            "display_id": "INC-002",
            "title": "No analysis yet",
            "status": "OPEN",
            "grafana_url": None,
            "log_filename": None,
            "screenshots": [],
            "triggered_at": "2026-09-13T02:00:00+00:00",
            "recovered_at": None,
            "analysis": None,
        },
    ],
}


def test_compact_incident_summaries_extracts_expected_fields():
    compact = entrypoint._compact_incident_summaries(_SNAPSHOT)
    assert compact[0] == {
        "display_id": "INC-001",
        "title": "Payment service errors",
        "status": "RESOLVED",
        "severity_signal": "high",
        "main_error": "null pointer dereference",
        "impact": "NullPointerException in PaymentWorker.charge",
        "starts_at": "2026-09-13T01:00:00+00:00",
        "ends_at": "2026-09-13T01:30:00+00:00",
    }


def test_compact_incident_summaries_handles_incident_with_no_analysis():
    compact = entrypoint._compact_incident_summaries(_SNAPSHOT)
    assert compact[1]["severity_signal"] is None
    assert compact[1]["main_error"] is None
    assert compact[1]["impact"] is None


def test_manifest_projection_is_the_source_of_daily_report_context(monkeypatch):
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", pathlib.Path(__file__).resolve().parents[2] / "skills")
    manifest = entrypoint._load_manifest("daily-alert-report")
    projected = entrypoint._project_snapshot(_SNAPSHOT, manifest["input_projection"])
    assert projected["shift_starts_at"] == _SNAPSHOT["shift_starts_at"]
    incident = projected["incidents"][0]
    assert incident["report_fragment"]["summary"]["en"] == "NullPointerException in PaymentWorker.charge"
    assert incident["report_fragment"]["likely_cause"]["en"] == "null pointer dereference"
    assert "grafana_url" not in incident
    assert "screenshots" not in incident


def test_run_skill_sends_claude_only_the_compact_snapshot(monkeypatch, tmp_path):
    """The ReportPlan prompt contains compact facts and no storage/evidence
    authority. The bridge resolves references after the model responds."""
    captured_prompts = []

    def _fake_invoke(prompt, *, schema, model, effort, max_budget):
        captured_prompts.append(prompt)
        return (
            {
                "overview_en": "one incident, high severity",
                "overview_zh": "一起事件，高严重性",
                "cross_incident_findings_en": "No cross-incident correlation was found.",
                "cross_incident_findings_zh": "未发现跨事件关联。",
            },
            {},
        )

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke)
    monkeypatch.setenv("SKILL_EFFORT", "low")
    monkeypatch.setenv("SKILL_EFFORT_ESCALATION", "medium")

    skill_dir = tmp_path / "daily-alert-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: daily-alert-report\n---\nBody")
    (skill_dir / "output.schema.json").write_text("{\n  \"$comment\": \"Skill Registry (Reliability mission Batch B): single source of truth for daily-alert-report's output contract, mirroring log-triage-summary/output.schema.json's role.\",\n  \"type\": \"object\",\n  \"properties\": {\n    \"overview_en\": {\"type\": \"string\"},\n    \"overview_zh\": {\"type\": \"string\"},\n    \"cross_incident_findings_en\": {\"type\": \"string\"},\n    \"cross_incident_findings_zh\": {\"type\": \"string\"}\n  },\n  \"required\": [\n    \"overview_en\", \"overview_zh\",\n    \"cross_incident_findings_en\", \"cross_incident_findings_zh\"\n  ],\n  \"additionalProperties\": false\n}\n")
    (skill_dir / "skill.yaml").write_text("input_contract:\n  filename: snapshot.json\n  intro_text: 'Here is the frozen shift snapshot to report on:'\n")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    entrypoint.run_skill(json.dumps(_SNAPSHOT), "daily-alert-report")

    assert captured_prompts, "expected at least one Claude invocation"
    for prompt in captured_prompts:
        # compact fields present
        assert "Payment service errors" in prompt
        assert "null pointer dereference" in prompt
        # full snapshot-only fields must NOT be sent
        assert "grafana.example" not in prompt
        assert "payments.log" not in prompt
        assert "noc-evidence" not in prompt
        assert "recommended_action_en" not in prompt
        assert "key_finds" not in prompt
