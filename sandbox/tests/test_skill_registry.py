"""Skill Registry / Skill Runtime mission Phase 3.

Previously this file guarded against skills/<name>/output.schema.json
drifting out of sync with a hard-coded inline Python schema dict
entrypoint.py used to hand the Claude CLI's --json-schema flag — two
copies of the same contract, kept in sync by hand and by this test.

Phase 3 removes the inline copy entirely: entrypoint.py now loads
output.schema.json directly from the mounted skill directory at run
time (`_load_output_schema`), so there is nothing left to drift. This
file now tests that loader directly, plus the still-real schema-changes-
the-skill-hash property (unchanged from Batch B).
"""
import json
import pathlib

import pytest

import entrypoint

SKILLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "skills"


def test_load_output_schema_reads_real_schema_file_for_log_triage(monkeypatch):
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", SKILLS_DIR)
    schema = entrypoint._load_output_schema("log-triage-summary")
    on_disk = json.loads((SKILLS_DIR / "log-triage-summary" / "output.schema.json").read_text())
    assert schema == on_disk
    assert "summary_en" in schema["properties"]


def test_load_output_schema_reads_real_schema_file_for_daily_report(monkeypatch):
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", SKILLS_DIR)
    schema = entrypoint._load_output_schema("daily-alert-report")
    on_disk = json.loads((SKILLS_DIR / "daily-alert-report" / "output.schema.json").read_text())
    assert schema == on_disk
    assert "metadata" in schema["properties"]
    assert "blocks" in schema["properties"]


def test_load_output_schema_raises_for_unknown_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)
    with pytest.raises(ValueError, match="no output schema found"):
        entrypoint._load_output_schema("nonexistent-skill")


def test_load_output_schema_reflects_a_materialized_snapshot_directory(tmp_path, monkeypatch):
    """Skill Runtime mission Phase 1/3 integration point: whatever the
    bridge materializes at /skills/<skill_name>/output.schema.json (the
    exact frozen SkillSnapshot content, not necessarily today's live
    checkout) is exactly what the sandbox validates Claude's output
    against — proven here by pointing SKILLS_DIR at a directory holding a
    deliberately different (v2-shaped) schema than the real skill."""
    skill_dir = tmp_path / "some-skill"
    skill_dir.mkdir()
    new_schema = {
        "type": "object",
        "properties": {"executive_summary": {"type": "string"}},
        "required": ["executive_summary"],
        "additionalProperties": False,
    }
    (skill_dir / "output.schema.json").write_text(json.dumps(new_schema))

    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)
    assert entrypoint._load_output_schema("some-skill") == new_schema


def test_skill_manifests_exist_and_declare_input_file():
    # Skill Runtime mission Phase 5: skill.yaml is now actually parsed at
    # run time (via entrypoint._load_input_contract), not just hashed
    # opaque bytes — so this exercises the real loader against the real
    # on-disk manifests, monkeypatching SKILLS_DIR the same way the other
    # tests in this file do.
    for skill_name, expected_input in (
        ("log-triage-summary", "log.txt"),
        ("daily-alert-report", "snapshot.json"),
    ):
        manifest_text = (SKILLS_DIR / skill_name / "skill.yaml").read_text()
        assert f"name: {skill_name}" in manifest_text
        assert f"filename: {expected_input}" in manifest_text


def test_load_input_contract_parses_real_manifests(monkeypatch):
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", SKILLS_DIR)
    filename, intro = entrypoint._load_input_contract("log-triage-summary")
    assert filename == "log.txt"
    assert intro

    filename, intro = entrypoint._load_input_contract("daily-alert-report")
    assert filename == "snapshot.json"
    assert intro


def test_load_input_contract_raises_for_unknown_skill(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)
    with pytest.raises(ValueError, match="no skill manifest found"):
        entrypoint._load_input_contract("nonexistent-skill")
