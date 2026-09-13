"""Tests for sandbox/entrypoint.py's Phase 4 effort-escalation policy:
default to low effort, escalate to a higher tier only when the low-effort
result itself signals it isn't good enough."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_ENTRYPOINT_PATH = pathlib.Path(__file__).resolve().parents[1] / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("sandbox_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(entrypoint)


def _analysis(confidence=0.9, severity_signal="low", key_finds=None):
    return {
        "confidence": confidence,
        "severity_signal": severity_signal,
        "key_finds": key_finds if key_finds is not None else [{"label_en": "x"}],
    }


def test_high_confidence_low_severity_does_not_escalate():
    assert entrypoint._escalation_reason(_analysis(confidence=0.9, severity_signal="low")) is None


def test_missing_confidence_escalates():
    result = {"severity_signal": "low", "key_finds": [{"label_en": "x"}]}
    reason = entrypoint._escalation_reason(result)
    assert reason is not None
    assert "confidence" in reason


def test_empty_key_finds_escalates():
    reason = entrypoint._escalation_reason(_analysis(key_finds=[]))
    assert reason is not None
    assert "key_finds" in reason


def test_low_confidence_below_threshold_escalates(monkeypatch):
    monkeypatch.setenv("SKILL_ESCALATION_CONFIDENCE_THRESHOLD", "0.6")
    reason = entrypoint._escalation_reason(_analysis(confidence=0.4, severity_signal="low"))
    assert reason is not None
    assert "threshold" in reason


def test_critical_severity_with_borderline_confidence_escalates():
    reason = entrypoint._escalation_reason(_analysis(confidence=0.6, severity_signal="critical"))
    assert reason is not None
    assert "critical" in reason


def test_critical_severity_with_high_confidence_does_not_escalate():
    assert entrypoint._escalation_reason(_analysis(confidence=0.95, severity_signal="critical")) is None


def test_effort_rank_orders_low_below_medium_below_high():
    assert entrypoint._EFFORT_RANK["low"] < entrypoint._EFFORT_RANK["medium"] < entrypoint._EFFORT_RANK["high"]


def test_run_skill_escalates_once_when_low_effort_result_is_uncertain(monkeypatch, tmp_path):
    """End-to-end (within entrypoint.py) check that a low-confidence
    low-effort result triggers exactly one escalated retry, never a loop."""
    calls = []

    def _fake_invoke(prompt, *, schema, model, effort, max_budget):
        calls.append(effort)
        if effort == "low":
            return _analysis(confidence=0.2, severity_signal="low"), {}
        return _analysis(confidence=0.95, severity_signal="low"), {}

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke)
    monkeypatch.setenv("SKILL_EFFORT", "low")
    monkeypatch.setenv("SKILL_EFFORT_ESCALATION", "medium")

    skill_dir = tmp_path / "log-triage-summary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: log-triage-summary\n---\nBody")
    (skill_dir / "output.schema.json").write_text("{\n  \"$comment\": \"Skill Registry (Reliability mission Batch B): the single source of truth for log-triage-summary's output contract. sandbox/entrypoint.py loads this file at run time instead of the inline dict it used to hard-code, so a schema change here is automatically reflected in both the sandbox's --json-schema enforcement AND this skill's content hash (see apps/api/app/skills/registry.py and bridge/noc_bridge/skill_registry.py) — no more editing two places and hoping they stay in sync.\",\n  \"type\": \"object\",\n  \"properties\": {\n    \"summary_en\": {\"type\": \"string\"},\n    \"summary_zh\": {\"type\": \"string\"},\n    \"key_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"},\n      \"minItems\": 1\n    },\n    \"secondary_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"}\n    },\n    \"likely_cause_en\": {\"type\": \"string\"},\n    \"likely_cause_zh\": {\"type\": \"string\"},\n    \"severity_signal\": {\"type\": \"string\", \"enum\": [\"low\", \"medium\", \"high\", \"critical\"]},\n    \"recommended_action_en\": {\"type\": \"string\"},\n    \"recommended_action_zh\": {\"type\": \"string\"},\n    \"confidence\": {\"type\": \"number\", \"minimum\": 0.0, \"maximum\": 1.0}\n  },\n  \"required\": [\n    \"summary_en\", \"summary_zh\", \"key_finds\", \"secondary_finds\",\n    \"likely_cause_en\", \"likely_cause_zh\", \"severity_signal\",\n    \"recommended_action_en\", \"recommended_action_zh\", \"confidence\"\n  ],\n  \"additionalProperties\": false,\n  \"$defs\": {\n    \"find\": {\n      \"type\": \"object\",\n      \"properties\": {\n        \"label_en\": {\"type\": \"string\"},\n        \"label_zh\": {\"type\": \"string\"},\n        \"count\": {\"type\": [\"integer\", \"null\"]},\n        \"percentage\": {\"type\": [\"number\", \"null\"]},\n        \"detail_en\": {\"type\": \"string\"},\n        \"detail_zh\": {\"type\": \"string\"}\n      },\n      \"required\": [\"label_en\", \"label_zh\", \"count\", \"percentage\", \"detail_en\", \"detail_zh\"],\n      \"additionalProperties\": false\n    }\n  }\n}\n")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    result, telemetry = entrypoint.run_skill("a short log line", "log-triage-summary")

    assert calls == ["low", "medium"]
    assert result["confidence"] == 0.95
    assert telemetry["escalated"] is True
    assert telemetry["effort"] == "medium"
    assert telemetry["escalation_reason"] is not None


def test_run_skill_escalation_telemetry_is_cumulative_not_overwritten(monkeypatch, tmp_path):
    """AI cost-optimization mission Phase 2, Issue 4: total usage after an
    escalation must be the SUM of the initial (low) and escalation
    (medium) calls, not just the escalated call's own numbers overwriting
    the first — both real calls were real spend."""

    def _envelope(input_tokens, output_tokens, cache_read, cache_creation, cost, duration, turns):
        return {
            "total_cost_usd": cost,
            "duration_ms": duration,
            "num_turns": turns,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        }

    def _fake_invoke(prompt, *, schema, model, effort, max_budget):
        if effort == "low":
            return _analysis(confidence=0.2, severity_signal="low"), _envelope(100, 50, 10, 5, 0.01, 1000, 2)
        return _analysis(confidence=0.95, severity_signal="low"), _envelope(200, 80, 20, 8, 0.05, 2000, 3)

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke)
    monkeypatch.setenv("SKILL_EFFORT", "low")
    monkeypatch.setenv("SKILL_EFFORT_ESCALATION", "medium")

    skill_dir = tmp_path / "log-triage-summary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: log-triage-summary\n---\nBody")
    (skill_dir / "output.schema.json").write_text("{\n  \"$comment\": \"Skill Registry (Reliability mission Batch B): the single source of truth for log-triage-summary's output contract. sandbox/entrypoint.py loads this file at run time instead of the inline dict it used to hard-code, so a schema change here is automatically reflected in both the sandbox's --json-schema enforcement AND this skill's content hash (see apps/api/app/skills/registry.py and bridge/noc_bridge/skill_registry.py) — no more editing two places and hoping they stay in sync.\",\n  \"type\": \"object\",\n  \"properties\": {\n    \"summary_en\": {\"type\": \"string\"},\n    \"summary_zh\": {\"type\": \"string\"},\n    \"key_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"},\n      \"minItems\": 1\n    },\n    \"secondary_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"}\n    },\n    \"likely_cause_en\": {\"type\": \"string\"},\n    \"likely_cause_zh\": {\"type\": \"string\"},\n    \"severity_signal\": {\"type\": \"string\", \"enum\": [\"low\", \"medium\", \"high\", \"critical\"]},\n    \"recommended_action_en\": {\"type\": \"string\"},\n    \"recommended_action_zh\": {\"type\": \"string\"},\n    \"confidence\": {\"type\": \"number\", \"minimum\": 0.0, \"maximum\": 1.0}\n  },\n  \"required\": [\n    \"summary_en\", \"summary_zh\", \"key_finds\", \"secondary_finds\",\n    \"likely_cause_en\", \"likely_cause_zh\", \"severity_signal\",\n    \"recommended_action_en\", \"recommended_action_zh\", \"confidence\"\n  ],\n  \"additionalProperties\": false,\n  \"$defs\": {\n    \"find\": {\n      \"type\": \"object\",\n      \"properties\": {\n        \"label_en\": {\"type\": \"string\"},\n        \"label_zh\": {\"type\": \"string\"},\n        \"count\": {\"type\": [\"integer\", \"null\"]},\n        \"percentage\": {\"type\": [\"number\", \"null\"]},\n        \"detail_en\": {\"type\": \"string\"},\n        \"detail_zh\": {\"type\": \"string\"}\n      },\n      \"required\": [\"label_en\", \"label_zh\", \"count\", \"percentage\", \"detail_en\", \"detail_zh\"],\n      \"additionalProperties\": false\n    }\n  }\n}\n")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    _result, telemetry = entrypoint.run_skill("a short log line", "log-triage-summary")

    assert telemetry["attempt_count"] == 2
    # Initial attempt's own numbers preserved separately.
    assert telemetry["initial_input_tokens"] == 100
    assert telemetry["initial_estimated_cost_usd"] == 0.01
    # Escalation call's own numbers.
    assert telemetry["escalation_input_tokens"] == 200
    assert telemetry["escalation_estimated_cost_usd"] == 0.05
    # Totals are the sum of both real calls, not just the escalated one.
    assert telemetry["input_tokens"] == 300
    assert telemetry["output_tokens"] == 130
    assert telemetry["cache_read_tokens"] == 30
    assert telemetry["cache_creation_tokens"] == 13
    assert telemetry["duration_ms"] == 3000
    assert telemetry["cost_usd"] == pytest.approx(0.06)


def test_run_skill_non_escalated_telemetry_totals_equal_initial(monkeypatch, tmp_path):
    """A non-escalated run made exactly one call, so its totals and its
    initial_* fields should be identical (attempt_count == 1)."""

    def _fake_invoke(prompt, *, schema, model, effort, max_budget):
        return _analysis(confidence=0.9, severity_signal="low"), {
            "total_cost_usd": 0.02,
            "duration_ms": 900,
            "num_turns": 2,
            "usage": {
                "input_tokens": 50,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke)
    monkeypatch.setenv("SKILL_EFFORT", "low")
    monkeypatch.setenv("SKILL_EFFORT_ESCALATION", "medium")

    skill_dir = tmp_path / "log-triage-summary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: log-triage-summary\n---\nBody")
    (skill_dir / "output.schema.json").write_text("{\n  \"$comment\": \"Skill Registry (Reliability mission Batch B): the single source of truth for log-triage-summary's output contract. sandbox/entrypoint.py loads this file at run time instead of the inline dict it used to hard-code, so a schema change here is automatically reflected in both the sandbox's --json-schema enforcement AND this skill's content hash (see apps/api/app/skills/registry.py and bridge/noc_bridge/skill_registry.py) — no more editing two places and hoping they stay in sync.\",\n  \"type\": \"object\",\n  \"properties\": {\n    \"summary_en\": {\"type\": \"string\"},\n    \"summary_zh\": {\"type\": \"string\"},\n    \"key_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"},\n      \"minItems\": 1\n    },\n    \"secondary_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"}\n    },\n    \"likely_cause_en\": {\"type\": \"string\"},\n    \"likely_cause_zh\": {\"type\": \"string\"},\n    \"severity_signal\": {\"type\": \"string\", \"enum\": [\"low\", \"medium\", \"high\", \"critical\"]},\n    \"recommended_action_en\": {\"type\": \"string\"},\n    \"recommended_action_zh\": {\"type\": \"string\"},\n    \"confidence\": {\"type\": \"number\", \"minimum\": 0.0, \"maximum\": 1.0}\n  },\n  \"required\": [\n    \"summary_en\", \"summary_zh\", \"key_finds\", \"secondary_finds\",\n    \"likely_cause_en\", \"likely_cause_zh\", \"severity_signal\",\n    \"recommended_action_en\", \"recommended_action_zh\", \"confidence\"\n  ],\n  \"additionalProperties\": false,\n  \"$defs\": {\n    \"find\": {\n      \"type\": \"object\",\n      \"properties\": {\n        \"label_en\": {\"type\": \"string\"},\n        \"label_zh\": {\"type\": \"string\"},\n        \"count\": {\"type\": [\"integer\", \"null\"]},\n        \"percentage\": {\"type\": [\"number\", \"null\"]},\n        \"detail_en\": {\"type\": \"string\"},\n        \"detail_zh\": {\"type\": \"string\"}\n      },\n      \"required\": [\"label_en\", \"label_zh\", \"count\", \"percentage\", \"detail_en\", \"detail_zh\"],\n      \"additionalProperties\": false\n    }\n  }\n}\n")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    _result, telemetry = entrypoint.run_skill("a short log line", "log-triage-summary")

    assert telemetry["attempt_count"] == 1
    assert telemetry["escalated"] is False
    assert telemetry["input_tokens"] == telemetry["initial_input_tokens"] == 50
    assert telemetry["escalation_input_tokens"] is None


def test_run_skill_does_not_escalate_when_low_effort_result_is_confident(monkeypatch, tmp_path):
    calls = []

    def _fake_invoke(prompt, *, schema, model, effort, max_budget):
        calls.append(effort)
        return _analysis(confidence=0.9, severity_signal="low"), {}

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke)
    monkeypatch.setenv("SKILL_EFFORT", "low")
    monkeypatch.setenv("SKILL_EFFORT_ESCALATION", "medium")

    skill_dir = tmp_path / "log-triage-summary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: log-triage-summary\n---\nBody")
    (skill_dir / "output.schema.json").write_text("{\n  \"$comment\": \"Skill Registry (Reliability mission Batch B): the single source of truth for log-triage-summary's output contract. sandbox/entrypoint.py loads this file at run time instead of the inline dict it used to hard-code, so a schema change here is automatically reflected in both the sandbox's --json-schema enforcement AND this skill's content hash (see apps/api/app/skills/registry.py and bridge/noc_bridge/skill_registry.py) — no more editing two places and hoping they stay in sync.\",\n  \"type\": \"object\",\n  \"properties\": {\n    \"summary_en\": {\"type\": \"string\"},\n    \"summary_zh\": {\"type\": \"string\"},\n    \"key_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"},\n      \"minItems\": 1\n    },\n    \"secondary_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"}\n    },\n    \"likely_cause_en\": {\"type\": \"string\"},\n    \"likely_cause_zh\": {\"type\": \"string\"},\n    \"severity_signal\": {\"type\": \"string\", \"enum\": [\"low\", \"medium\", \"high\", \"critical\"]},\n    \"recommended_action_en\": {\"type\": \"string\"},\n    \"recommended_action_zh\": {\"type\": \"string\"},\n    \"confidence\": {\"type\": \"number\", \"minimum\": 0.0, \"maximum\": 1.0}\n  },\n  \"required\": [\n    \"summary_en\", \"summary_zh\", \"key_finds\", \"secondary_finds\",\n    \"likely_cause_en\", \"likely_cause_zh\", \"severity_signal\",\n    \"recommended_action_en\", \"recommended_action_zh\", \"confidence\"\n  ],\n  \"additionalProperties\": false,\n  \"$defs\": {\n    \"find\": {\n      \"type\": \"object\",\n      \"properties\": {\n        \"label_en\": {\"type\": \"string\"},\n        \"label_zh\": {\"type\": \"string\"},\n        \"count\": {\"type\": [\"integer\", \"null\"]},\n        \"percentage\": {\"type\": [\"number\", \"null\"]},\n        \"detail_en\": {\"type\": \"string\"},\n        \"detail_zh\": {\"type\": \"string\"}\n      },\n      \"required\": [\"label_en\", \"label_zh\", \"count\", \"percentage\", \"detail_en\", \"detail_zh\"],\n      \"additionalProperties\": false\n    }\n  }\n}\n")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    _result, telemetry = entrypoint.run_skill("a short log line", "log-triage-summary")

    assert calls == ["low"]
    assert telemetry["escalated"] is False
    assert telemetry["escalation_reason"] is None
