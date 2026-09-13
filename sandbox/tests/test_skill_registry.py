"""Skill Registry (Reliability mission Batch B): guards against
skills/<name>/output.schema.json drifting out of sync with the inline
schema entrypoint.py actually hands the Claude CLI's --json-schema flag.

entrypoint.py keeps its schemas inline (rather than loading
output.schema.json at run time) because that's the schema shape already
proven to work against the real CLI invocation — loading from disk at
run time would be a real behavior change to a live external-process
integration that isn't safely exercisable here. Instead, this test is
the drift guard: the two must describe the same contract, and a change
to one without the other fails CI.
"""
import json
import pathlib

import entrypoint

SKILLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "skills"


def _resolve_refs(node):
    """Inlines the one $ref shape output.schema.json uses
    (#/$defs/find) so it can be compared against entrypoint.py's already-
    expanded inline schema."""
    if isinstance(node, dict):
        if set(node.keys()) == {"$ref"} and node["$ref"] == "#/$defs/find":
            return _resolve_refs(_FIND_DEFS["find"])
        return {k: _resolve_refs(v) for k, v in node.items() if k != "$defs" and k != "$comment"}
    if isinstance(node, list):
        return [_resolve_refs(item) for item in node]
    return node


def _load_schema(skill_name: str) -> dict:
    global _FIND_DEFS
    raw = json.loads((SKILLS_DIR / skill_name / "output.schema.json").read_text())
    _FIND_DEFS = raw.get("$defs", {})
    return _resolve_refs(raw)


def test_log_triage_schema_file_matches_inline_schema():
    assert _load_schema("log-triage-summary") == entrypoint._SCHEMAS["log-triage-summary"]


def test_daily_report_schema_file_matches_inline_schema():
    assert _load_schema("daily-alert-report") == entrypoint._SCHEMAS["daily-alert-report"]


def test_skill_manifests_exist_and_declare_input_file():
    # No YAML parser dependency declared for sandbox/ (its Dockerfile
    # installs nothing beyond the stdlib) — the manifest's two fields
    # tested here are simple enough that a substring check avoids adding
    # one just for this test.
    for skill_name, expected_input in (
        ("log-triage-summary", "log.txt"),
        ("daily-alert-report", "snapshot.json"),
    ):
        manifest_text = (SKILLS_DIR / skill_name / "skill.yaml").read_text()
        assert f"name: {skill_name}" in manifest_text
        assert f"input_file: {expected_input}" in manifest_text
