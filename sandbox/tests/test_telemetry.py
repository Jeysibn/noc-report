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
        "total_model_input_tokens": 510,
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
    (skill_dir / "output.schema.json").write_text("{\n  \"$comment\": \"Skill Registry (Reliability mission Batch B): the single source of truth for log-triage-summary's output contract. sandbox/entrypoint.py loads this file at run time instead of the inline dict it used to hard-code, so a schema change here is automatically reflected in both the sandbox's --json-schema enforcement AND this skill's content hash (see apps/api/app/skills/registry.py and bridge/noc_bridge/skill_registry.py) — no more editing two places and hoping they stay in sync.\",\n  \"type\": \"object\",\n  \"properties\": {\n    \"summary_en\": {\"type\": \"string\"},\n    \"summary_zh\": {\"type\": \"string\"},\n    \"key_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"},\n      \"minItems\": 1\n    },\n    \"secondary_finds\": {\n      \"type\": \"array\",\n      \"items\": {\"$ref\": \"#/$defs/find\"}\n    },\n    \"likely_cause_en\": {\"type\": \"string\"},\n    \"likely_cause_zh\": {\"type\": \"string\"},\n    \"severity_signal\": {\"type\": \"string\", \"enum\": [\"low\", \"medium\", \"high\", \"critical\"]},\n    \"recommended_action_en\": {\"type\": \"string\"},\n    \"recommended_action_zh\": {\"type\": \"string\"},\n    \"confidence\": {\"type\": \"number\", \"minimum\": 0.0, \"maximum\": 1.0}\n  },\n  \"required\": [\n    \"summary_en\", \"summary_zh\", \"key_finds\", \"secondary_finds\",\n    \"likely_cause_en\", \"likely_cause_zh\", \"severity_signal\",\n    \"recommended_action_en\", \"recommended_action_zh\", \"confidence\"\n  ],\n  \"additionalProperties\": false,\n  \"$defs\": {\n    \"find\": {\n      \"type\": \"object\",\n      \"properties\": {\n        \"label_en\": {\"type\": \"string\"},\n        \"label_zh\": {\"type\": \"string\"},\n        \"count\": {\"type\": [\"integer\", \"null\"]},\n        \"percentage\": {\"type\": [\"number\", \"null\"]},\n        \"detail_en\": {\"type\": \"string\"},\n        \"detail_zh\": {\"type\": \"string\"}\n      },\n      \"required\": [\"label_en\", \"label_zh\", \"count\", \"percentage\", \"detail_en\", \"detail_zh\"],\n      \"additionalProperties\": false\n    }\n  }\n}\n")
    (skill_dir / "skill.yaml").write_text("input_contract:\n  filename: log.txt\n  intro_text: 'Here is the log excerpt to analyze:'\n")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(entrypoint, "MAX_LOG_CHARS", 10)  # force compaction on a tiny log

    log_text = "ERROR something failed\n" * 50
    result, telemetry = entrypoint.run_skill(log_text, "log-triage-summary")

    assert telemetry["raw_input_bytes"] > telemetry["evidence_bytes"]
    assert telemetry["preprocessing_ratio"] > 0
    assert telemetry["cost_usd"] == 0.01
    assert telemetry["confidence"] == 0.9
    assert telemetry["escalated"] is False
