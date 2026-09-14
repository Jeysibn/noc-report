"""Skill Registry (Reliability mission Batch B/D)."""
import pytest

from app.models.models import SkillSnapshot
from app.skills.registry import (
    NoActiveSkillSnapshot,
    SkillVersionNotFound,
    compute_skill_hash,
    compute_execution_hash,
    get_or_create_snapshot,
    list_skill_names,
    list_snapshots,
    resolve_active_snapshot,
    set_active_snapshot,
)


def test_compute_skill_hash_matches_real_skills_dir():
    # Exercises the real skills/ directory (same one the bridge and
    # sandbox mount) — not a fixture copy, so a genuinely missing/renamed
    # file in either skill would fail this test rather than a mock.
    assert len(compute_skill_hash("log-triage-summary")) == 64
    assert len(compute_skill_hash("daily-alert-report")) == 64


def test_get_or_create_snapshot_is_idempotent_for_unchanged_content(db_session):
    first = get_or_create_snapshot(db_session, "log-triage-summary")
    db_session.commit()
    second = get_or_create_snapshot(db_session, "log-triage-summary")
    db_session.commit()

    assert first.id == second.id
    assert first.content_hash == second.content_hash
    assert first.version_label == 1

    count = db_session.query(SkillSnapshot).filter_by(skill_name="log-triage-summary").count()
    assert count == 1


def test_get_or_create_snapshot_creates_new_version_on_content_change(db_session, tmp_path):
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1 prompt")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")

    first = get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()
    assert first.version_label == 1

    (skill_dir / "SKILL.md").write_text("v2 prompt — content changed")
    second = get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()

    assert second.id != first.id
    assert second.content_hash != first.content_hash
    assert second.version_label == 2

    # The old snapshot is untouched — immutable history, not overwritten.
    db_session.refresh(first)
    assert first.skill_md == "v1 prompt"


def test_snapshots_are_distinct_per_skill_name(db_session):
    log_triage = get_or_create_snapshot(db_session, "log-triage-summary")
    daily_report = get_or_create_snapshot(db_session, "daily-alert-report")
    db_session.commit()

    assert log_triage.content_hash != daily_report.content_hash
    assert log_triage.skill_name == "log-triage-summary"
    assert daily_report.skill_name == "daily-alert-report"


def test_first_snapshot_bootstraps_active_but_later_ones_are_draft(db_session, tmp_path):
    """Skill Runtime mission Phase 2: the very first snapshot ever seen
    for a skill_name bootstraps as active (there must always be an active
    snapshot once a skill has any history), but a later content change is
    registered as a Draft — it does NOT silently become what new jobs
    execute. An admin must explicitly activate it via set_active_snapshot
    (see resolve_active_snapshot, the real execution-controlling lookup)."""
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1 prompt")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")

    first = get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()
    assert first.is_active is True

    (skill_dir / "SKILL.md").write_text("v2 prompt — content changed")
    second = get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()

    db_session.refresh(first)
    assert first.is_active is True
    assert second.is_active is False


def test_set_active_snapshot_moves_active_flag_and_is_reversible(db_session, tmp_path):
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1 prompt")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")

    v1 = get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()

    (skill_dir / "SKILL.md").write_text("v2 prompt — content changed")
    v2 = get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()
    assert v2.is_active is False  # Draft until explicitly activated

    set_active_snapshot(db_session, "fake-skill", v2.version_label)
    db_session.commit()
    db_session.refresh(v2)
    assert v2.is_active is True

    rolled_back = set_active_snapshot(db_session, "fake-skill", v1.version_label)
    db_session.commit()
    assert rolled_back.id == v1.id
    assert rolled_back.is_active is True

    db_session.refresh(v2)
    assert v2.is_active is False

    # Reversible: re-activating v2 flips it back.
    reactivated = set_active_snapshot(db_session, "fake-skill", v2.version_label)
    db_session.commit()
    assert reactivated.id == v2.id
    assert reactivated.is_active is True
    db_session.refresh(v1)
    assert v1.is_active is False


def test_set_active_snapshot_raises_on_unknown_version(db_session):
    get_or_create_snapshot(db_session, "log-triage-summary")
    db_session.commit()
    with pytest.raises(SkillVersionNotFound):
        set_active_snapshot(db_session, "log-triage-summary", 9999)


def test_list_snapshots_orders_newest_first(db_session, tmp_path):
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")
    get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()
    (skill_dir / "SKILL.md").write_text("v2")
    get_or_create_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()

    snapshots = list_snapshots(db_session, "fake-skill")
    assert [s.version_label for s in snapshots] == [2, 1]


def test_resolve_active_snapshot_bootstraps_on_first_call(db_session, tmp_path):
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1 prompt")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")

    resolved = resolve_active_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()
    assert resolved.version_label == 1
    assert resolved.is_active is True


def test_resolve_active_snapshot_ignores_disk_drift_until_activated(db_session, tmp_path):
    """Core Skill Runtime mission Phase 2 requirement: editing SKILL.md on
    disk and calling resolve_active_snapshot again must keep resolving the
    still-active older version — new jobs are not silently repointed at
    unreviewed on-disk content."""
    skill_dir = tmp_path / "fake-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("v1 prompt")
    (skill_dir / "output.schema.json").write_text("{}")
    (skill_dir / "skill.yaml").write_text("name: fake-skill\n")

    v1 = resolve_active_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()

    (skill_dir / "SKILL.md").write_text("v2 prompt — changed on disk")
    still_v1 = resolve_active_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()

    assert still_v1.id == v1.id
    assert still_v1.version_label == 1

    # The new content was registered (for history/audit) as a Draft.
    snapshots = list_snapshots(db_session, "fake-skill")
    assert [s.version_label for s in snapshots] == [2, 1]
    assert {s.version_label: s.is_active for s in snapshots} == {1: True, 2: False}

    # Once activated, the *next* resolve call picks it up.
    set_active_snapshot(db_session, "fake-skill", 2)
    db_session.commit()
    now_v2 = resolve_active_snapshot(db_session, "fake-skill", skills_dir=tmp_path)
    db_session.commit()
    assert now_v2.version_label == 2


def test_list_skill_names(db_session):
    get_or_create_snapshot(db_session, "log-triage-summary")
    get_or_create_snapshot(db_session, "daily-alert-report")
    db_session.commit()
    names = list_skill_names(db_session)
    assert "log-triage-summary" in names
    assert "daily-alert-report" in names


def test_dependency_snapshot_change_creates_new_execution_identity(db_session, tmp_path):
    dep_dir = tmp_path / "dependency"
    root_dir = tmp_path / "root"
    dep_dir.mkdir()
    root_dir.mkdir()
    (dep_dir / "SKILL.md").write_text("dependency v1")
    (dep_dir / "output.schema.json").write_text("{}")
    (dep_dir / "skill.yaml").write_text("id: dependency\nversion: '1'\n")
    (root_dir / "SKILL.md").write_text("root stays unchanged")
    (root_dir / "output.schema.json").write_text("{}")
    (root_dir / "skill.yaml").write_text("id: root\ndependencies:\n  - dependency\n")

    dep_v1 = get_or_create_snapshot(db_session, "dependency", skills_dir=tmp_path)
    root_v1 = get_or_create_snapshot(db_session, "root", skills_dir=tmp_path)
    db_session.commit()
    first_execution_hash = compute_execution_hash(db_session, root_v1)

    (dep_dir / "SKILL.md").write_text("dependency v2")
    dep_v2 = get_or_create_snapshot(db_session, "dependency", skills_dir=tmp_path)
    set_active_snapshot(db_session, "dependency", dep_v2.version_label)
    root_v2 = get_or_create_snapshot(db_session, "root", skills_dir=tmp_path)
    db_session.commit()

    assert dep_v1.id != dep_v2.id
    assert root_v1.content_hash == root_v2.content_hash
    assert root_v1.id != root_v2.id
    assert first_execution_hash != compute_execution_hash(db_session, root_v2)
    assert root_v1.dependency_snapshot_ids["dependency"] == str(dep_v1.id)
    assert root_v2.dependency_snapshot_ids["dependency"] == str(dep_v2.id)
