"""Tests for sandbox/entrypoint.py's Phase 2 (Issue 2) robust,
version-tolerant parsing of a `claude -p --output-format json` envelope:
prefer `structured_output` (already-validated dict) -> `result` (string or
dict) -> the envelope itself, and fail loudly (not silently) when none of
those yield a usable dict."""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_ENTRYPOINT_PATH = pathlib.Path(__file__).resolve().parents[1] / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("sandbox_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(entrypoint)

_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def test_prefers_structured_output_when_present():
    envelope = {
        "structured_output": {"x": "from structured_output"},
        "result": json.dumps({"x": "from result"}),
    }
    result = entrypoint._parse_claude_result(envelope, schema=_SCHEMA)
    assert result == {"x": "from structured_output"}


def test_falls_back_to_result_as_json_string_when_no_structured_output():
    envelope = {"result": json.dumps({"x": "from result"})}
    result = entrypoint._parse_claude_result(envelope, schema=_SCHEMA)
    assert result == {"x": "from result"}


def test_falls_back_to_result_as_dict_when_no_structured_output():
    envelope = {"result": {"x": "already a dict"}}
    result = entrypoint._parse_claude_result(envelope, schema=_SCHEMA)
    assert result == {"x": "already a dict"}


def test_falls_back_to_envelope_itself_when_no_result_or_structured_output():
    envelope = {"x": "bare envelope"}
    result = entrypoint._parse_claude_result(envelope, schema=_SCHEMA)
    assert result == {"x": "bare envelope"}


def test_ignores_non_dict_structured_output_and_falls_back_to_result():
    # Defensive: if a future CLI version ever sent structured_output as
    # something other than a dict (e.g. null on a failed validation),
    # don't silently accept it as the result.
    envelope = {"structured_output": None, "result": json.dumps({"x": "from result"})}
    result = entrypoint._parse_claude_result(envelope, schema=_SCHEMA)
    assert result == {"x": "from result"}


def test_invalid_envelope_shape_raises():
    with pytest.raises(ValueError):
        entrypoint._parse_claude_result("not a dict", schema=_SCHEMA)


def test_unparseable_result_string_raises():
    envelope = {"result": "not valid json {{{"}
    with pytest.raises(ValueError):
        entrypoint._parse_claude_result(envelope, schema=_SCHEMA)


def test_non_dict_result_raises():
    envelope = {"result": json.dumps(["not", "a", "dict"])}
    with pytest.raises(ValueError):
        entrypoint._parse_claude_result(envelope, schema=_SCHEMA)


def test_invoke_claude_retries_once_then_succeeds(monkeypatch):
    """Phase 13: a single bad structured-output parse doesn't fail the job
    outright — it gets one bounded retry at the same model/effort."""
    calls = {"n": 0}

    def fake_invoke_once(prompt, *, schema, model, effort, max_budget):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("claude CLI result was not valid JSON")
        return {"x": "ok"}, {"total_cost_usd": 0.01}

    monkeypatch.setattr(entrypoint, "_invoke_claude_once", fake_invoke_once)
    result, envelope = entrypoint._invoke_claude(
        "prompt", schema=_SCHEMA, model="m", effort="low", max_budget="0.50"
    )
    assert result == {"x": "ok"}
    assert calls["n"] == 2


def test_invoke_claude_raises_structured_output_retry_exhausted_after_two_failures(monkeypatch):
    def fake_invoke_once(prompt, *, schema, model, effort, max_budget):
        raise ValueError("claude CLI result was not valid JSON")

    monkeypatch.setattr(entrypoint, "_invoke_claude_once", fake_invoke_once)
    with pytest.raises(RuntimeError, match="structured_output_retry_exhausted"):
        entrypoint._invoke_claude("prompt", schema=_SCHEMA, model="m", effort="low", max_budget="0.50")


def test_invoke_claude_does_not_retry_cli_level_failure(monkeypatch):
    """A nonzero-exit CLI failure is a distinct RuntimeError raised
    immediately by _invoke_claude_once — not the retried ValueError path."""
    calls = {"n": 0}

    def fake_invoke_once(prompt, *, schema, model, effort, max_budget):
        calls["n"] += 1
        raise RuntimeError("claude CLI exited 1: some api error")

    monkeypatch.setattr(entrypoint, "_invoke_claude_once", fake_invoke_once)
    with pytest.raises(RuntimeError, match="some api error"):
        entrypoint._invoke_claude("prompt", schema=_SCHEMA, model="m", effort="low", max_budget="0.50")
    assert calls["n"] == 1
