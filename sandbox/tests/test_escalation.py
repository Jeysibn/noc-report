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
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    result = entrypoint.run_skill("a short log line", "log-triage-summary")

    assert calls == ["low", "medium"]
    assert result["confidence"] == 0.95


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
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    entrypoint.run_skill("a short log line", "log-triage-summary")

    assert calls == ["low"]
