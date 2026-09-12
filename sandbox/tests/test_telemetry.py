"""Tests for sandbox/entrypoint.py's Phase 1 AI usage telemetry: run_skill
returns (result, telemetry), and telemetry degrades gracefully when the
CLI envelope is missing fields rather than raising."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_ENTRYPOINT_PATH = pathlib.Path(__file__).resolve().parents[1] / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("sandbox_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(entrypoint)


def test_envelope_telemetry_extracts_known_fields():
    envelope = {
        "total_cost_usd": 0.0123,
        "duration_ms": 4200,
        "num_turns": 1,
        "usage": {
            "input_tokens": 500,
            "output_tokens": 120,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 0,
        },
    }
    telemetry = entrypoint._envelope_telemetry(envelope)
    assert telemetry == {
        "cost_usd": 0.0123,
        "duration_ms": 4200,
        "num_turns": 1,
        "input_tokens": 500,
        "output_tokens": 120,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 0,
    }


def test_envelope_telemetry_degrades_to_nulls_on_missing_fields():
    telemetry = entrypoint._envelope_telemetry({})
    assert all(v is None for v in telemetry.values())


def test_run_skill_reports_preprocessing_ratio(monkeypatch, tmp_path):
    def _fake_invoke(prompt, *, schema, model, effort, max_budget):
        return {"confidence": 0.9, "severity_signal": "low", "key_finds": [{"label_en": "x"}]}, {
            "total_cost_usd": 0.01,
            "duration_ms": 100,
            "num_turns": 1,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke)
    skill_dir = tmp_path / "log-triage-summary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: log-triage-summary\n---\nBody")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(entrypoint, "MAX_LOG_CHARS", 10)  # force compaction on a tiny log

    log_text = "ERROR something failed\n" * 50
    result, telemetry = entrypoint.run_skill(log_text, "log-triage-summary")

    assert telemetry["raw_input_bytes"] > telemetry["evidence_bytes"]
    assert telemetry["preprocessing_ratio"] > 0
    assert telemetry["cost_usd"] == 0.01
    assert telemetry["confidence"] == 0.9
    assert telemetry["escalated"] is False
