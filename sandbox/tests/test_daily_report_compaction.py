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


def test_run_skill_sends_claude_only_the_compact_snapshot(monkeypatch, tmp_path):
    """The core Issue 6 guarantee: the prompt actually handed to Claude for
    daily-alert-report contains the compact fields, but never the full
    per-incident analysis object, screenshots, or Grafana/log-filename
    references — those are merged back in deterministically by the bridge
    afterward (see bridge/noc_bridge/service.py's _merge_daily_report),
    never sent to or reproduced by the model."""
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
