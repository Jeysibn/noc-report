"""Skill Registry — bridge-side hash/verify (Reliability mission Batch B)."""
import pathlib

import pytest

from noc_bridge.skill_registry import SkillHashMismatch, compute_skill_hash, verify_skill_hash

SKILLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "skills"


def test_compute_skill_hash_is_stable():
    first = compute_skill_hash("log-triage-summary", skills_dir=SKILLS_DIR)
    second = compute_skill_hash("log-triage-summary", skills_dir=SKILLS_DIR)
    assert first == second
    assert len(first) == 64  # sha256 hexdigest


def test_compute_skill_hash_differs_between_skills():
    log_triage = compute_skill_hash("log-triage-summary", skills_dir=SKILLS_DIR)
    daily_report = compute_skill_hash("daily-alert-report", skills_dir=SKILLS_DIR)
    assert log_triage != daily_report


def test_verify_skill_hash_noop_when_expected_is_none():
    verify_skill_hash("log-triage-summary", None, skills_dir=SKILLS_DIR)  # must not raise


def test_verify_skill_hash_passes_on_match():
    actual = compute_skill_hash("log-triage-summary", skills_dir=SKILLS_DIR)
    verify_skill_hash("log-triage-summary", actual, skills_dir=SKILLS_DIR)  # must not raise


def test_verify_skill_hash_raises_on_mismatch():
    with pytest.raises(SkillHashMismatch):
        verify_skill_hash("log-triage-summary", "0" * 64, skills_dir=SKILLS_DIR)


def test_verify_skill_hash_raises_when_skill_content_changes(tmp_path):
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")

    original_hash = compute_skill_hash("fake-skill", skills_dir=tmp_path)
    verify_skill_hash("fake-skill", original_hash, skills_dir=tmp_path)  # still matches

    (skill_dir / "SKILL.md").write_text("v2 — the prompt changed")
    with pytest.raises(SkillHashMismatch):
        verify_skill_hash("fake-skill", original_hash, skills_dir=tmp_path)


def test_classify_failure_treats_skill_hash_mismatch_as_terminal():
    from noc_bridge.failures import TERMINAL, classify_failure

    assert classify_failure(SkillHashMismatch("boom")) == TERMINAL
