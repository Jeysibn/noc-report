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
