"""Skill Registry — bridge-side hash/verify (Reliability mission Batch B)."""
import pathlib

import pytest

from noc_bridge.skill_registry import (
    SkillHashMismatch,
    SkillSnapshotMissing,
    compute_skill_hash,
    fetch_skill_snapshot,
    materialize_snapshot,
    verify_skill_hash,
)

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


class _FakeCursor:
    """Minimal RealDictCursor-shaped stand-in — no real Postgres needed to
    exercise fetch_skill_snapshot/materialize_snapshot's own logic."""

    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params):
        self._executed_hash = params[0]

    def fetchone(self):
        if self._row is not None and self._row["content_hash"] == self._executed_hash:
            return dict(self._row)
        return None


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._row)


class _GraphCursor:
    def __init__(self, rows):
        self._rows = rows
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params):
        if "WHERE id" in query:
            self._result = self._rows.get(("id", str(params[0])))
        elif "WHERE content_hash" in query:
            self._result = self._rows.get(("hash", params[0]))
        else:
            name = params[0]
            version = params[1] if len(params) > 1 else None
            matches = [row for row in self._rows.values() if row.get("skill_name") == name]
            if version is not None:
                matches = [row for row in matches if int(row["version_label"]) == int(version)]
            self._result = max(matches, key=lambda row: int(row["version_label"])) if matches else None

    def fetchone(self):
        return dict(self._result) if self._result is not None else None


class _GraphConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, cursor_factory=None):
        return _GraphCursor(self._rows)


_FAKE_SNAPSHOT_ROW = {
    "skill_name": "log-triage-summary",
    "version_label": "3",
    "content_hash": "abc123",
    "skill_md": "# v3 prompt content",
    "output_schema_json": '{"type": "object"}',
    "manifest_yaml": "name: log-triage-summary\nversion: 3\n",
}


def test_fetch_skill_snapshot_returns_none_when_hash_not_found():
    conn = _FakeConn(_FAKE_SNAPSHOT_ROW)
    assert fetch_skill_snapshot(conn, "does-not-exist") is None


def test_fetch_skill_snapshot_returns_matching_row():
    conn = _FakeConn(_FAKE_SNAPSHOT_ROW)
    row = fetch_skill_snapshot(conn, "abc123")
    assert row["skill_md"] == "# v3 prompt content"


def test_materialize_snapshot_writes_exact_db_content_not_live_disk(tmp_path):
    """Core Skill Runtime mission requirement: the materialized files come
    from the DB snapshot row, verbatim — regardless of what's on disk."""
    conn = _FakeConn(_FAKE_SNAPSHOT_ROW)
    dest_root = tmp_path / "job-scratch"

    result_root = materialize_snapshot(
        conn, "log-triage-summary", "abc123", dest_root=dest_root
    )

    assert result_root == dest_root
    skill_dir = dest_root / "log-triage-summary"
    assert (skill_dir / "SKILL.md").read_text() == "# v3 prompt content"
    assert (skill_dir / "output.schema.json").read_text() == '{"type": "object"}'
    assert (skill_dir / "skill.yaml").read_text() == "name: log-triage-summary\nversion: 3\n"


def test_materialize_snapshot_raises_terminal_error_when_snapshot_missing(tmp_path):
    conn = _FakeConn(None)
    with pytest.raises(SkillSnapshotMissing):
        materialize_snapshot(conn, "log-triage-summary", "some-hash", dest_root=tmp_path)


def test_materialize_snapshot_grants_sandbox_uid_read_access(tmp_path):
    """Regression test: dest_root is normally a fresh
    tempfile.TemporaryDirectory (0700, host-user-only), which alone blocks
    the sandbox's fixed non-root uid (10001) from reading anything under
    it once bind-mounted read-only at /skills — regardless of skill_dir's
    or the individual files' own modes. materialize_snapshot must widen
    exactly the "other" bits needed (traverse+list on both directories,
    read on each file) and grant no "group" bit."""
    import stat

    conn = _FakeConn(_FAKE_SNAPSHOT_ROW)
    dest_root = tmp_path / "job-scratch"
    dest_root.mkdir(mode=0o700)

    materialize_snapshot(conn, "log-triage-summary", "abc123", dest_root=dest_root)

    skill_dir = dest_root / "log-triage-summary"

    def mode(path):
        return stat.S_IMODE(path.stat().st_mode)

    assert mode(dest_root) == 0o705
    assert mode(skill_dir) == 0o705
    for filename in ("SKILL.md", "output.schema.json", "skill.yaml"):
        assert mode(skill_dir / filename) == 0o604


def test_materialize_snapshot_recursively_freezes_transitive_dependencies(tmp_path):
    rows = {}

    def add(row):
        rows[("id", row["id"])] = row
        rows[("hash", row["content_hash"])] = row
        rows[row["id"]] = row

    add({
        "id": "root-id",
        "skill_name": "root-skill",
        "version_label": "1",
        "content_hash": "root-hash",
        "skill_md": "root",
        "output_schema_json": "{}",
        "manifest_yaml": (
            "name: root-skill\n"
            "dependencies:\n"
            "  - id: dependency-a\n"
            "dependency_snapshot_ids:\n"
            "  dependency-a: dependency-a-id\n"
        ),
        "dependency_snapshot_ids": {"dependency-a": "dependency-a-id"},
    })
    add({
        "id": "dependency-a-id",
        "skill_name": "dependency-a",
        "version_label": "2",
        "content_hash": "dependency-a-hash",
        "skill_md": "dependency a",
        "output_schema_json": "{}",
        "manifest_yaml": "name: dependency-a\ndependencies:\n  - dependency-b\n",
        "dependency_snapshot_ids": {"dependency-b": "dependency-b-id"},
    })
    add({
        "id": "dependency-b-id",
        "skill_name": "dependency-b",
        "version_label": "3",
        "content_hash": "dependency-b-hash",
        "skill_md": "dependency b",
        "output_schema_json": "{}",
        "manifest_yaml": "name: dependency-b\ndependencies: []\n",
        "dependency_snapshot_ids": {},
    })

    materialize_snapshot(
        _GraphConn(rows), "root-skill", "root-hash", snapshot_id="root-id", dest_root=tmp_path
    )

    assert (tmp_path / "root-skill" / "SKILL.md").read_text() == "root"
    assert (tmp_path / "dependency-a" / "SKILL.md").read_text() == "dependency a"
    assert (tmp_path / "dependency-b" / "SKILL.md").read_text() == "dependency b"


def test_materialize_snapshot_rejects_dependency_cycles(tmp_path):
    rows = {}

    def add(row):
        rows[("id", row["id"])] = row
        rows[("hash", row["content_hash"])] = row
        rows[row["id"]] = row

    add({
        "id": "cycle-root",
        "skill_name": "cycle-root",
        "version_label": "1",
        "content_hash": "cycle-root-hash",
        "skill_md": "root",
        "output_schema_json": "{}",
        "manifest_yaml": "name: cycle-root\ndependencies:\n  - cycle-dep\n",
        "dependency_snapshot_ids": {"cycle-dep": "cycle-dep-id"},
    })
    add({
        "id": "cycle-dep-id",
        "skill_name": "cycle-dep",
        "version_label": "1",
        "content_hash": "cycle-dep-hash",
        "skill_md": "dep",
        "output_schema_json": "{}",
        "manifest_yaml": "name: cycle-dep\ndependencies:\n  - cycle-root\n",
        "dependency_snapshot_ids": {"cycle-root": "cycle-root"},
    })

    with pytest.raises(SkillSnapshotMissing, match="cyclic skill dependency"):
        materialize_snapshot(
            _GraphConn(rows), "cycle-root", "cycle-root-hash",
            snapshot_id="cycle-root", dest_root=tmp_path,
        )


def test_classify_failure_treats_missing_snapshot_as_terminal():
    from noc_bridge.failures import TERMINAL, classify_failure

    assert classify_failure(SkillSnapshotMissing("boom")) == TERMINAL
