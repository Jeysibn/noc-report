"""Skill Registry — bridge-side mirror (Reliability mission Batch B).

Mirrors `apps/api/app/skills/registry.py::compute_skill_hash` exactly
(same separate-deployable-kept-in-sync-by-hand convention as
`noc_bridge/db.py`/`noc_bridge/config.py`): sha256 over SKILL.md,
output.schema.json, and skill.yaml's bytes, each length-prefixed, in that
fixed order. The bridge recomputes this locally, from the same `skills/`
directory it already mounts into the sandbox (see
`BridgeSettings.skills_dir`), and compares it against the `skill_hash` the
API stamped into the job message at enqueue time — a mismatch means the
skill's content has drifted between when the job was enqueued and when
this worker is about to execute it (e.g. a deploy landed mid-flight, or a
message sat in the retry queue across a skill edit), which is exactly the
kind of silent-drift scenario the reliability mission calls out as
unacceptable for a "reproducible" pipeline.
"""
from __future__ import annotations

import hashlib
import pathlib

import psycopg2.extras

_PROMPT_FILENAME = "SKILL.md"
_SCHEMA_FILENAME = "output.schema.json"
_MANIFEST_FILENAME = "skill.yaml"


class SkillHashMismatch(RuntimeError):
    """Terminal (see noc_bridge/failures.py's _TERMINAL_MESSAGE_MARKERS —
    re-running the exact same job against the exact same drifted skill
    content would just reproduce the same mismatch)."""


def compute_skill_hash(skill_name: str, *, skills_dir: pathlib.Path) -> str:
    directory = skills_dir / skill_name
    digest = hashlib.sha256()
    for filename in (_PROMPT_FILENAME, _SCHEMA_FILENAME, _MANIFEST_FILENAME):
        contents = (directory / filename).read_bytes()
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def verify_skill_hash(skill_name: str, expected_hash: str | None, *, skills_dir: pathlib.Path) -> None:
    """No-op if `expected_hash` is None (an older/unmigrated caller, or a
    test, didn't stamp one) — a missing hash is "nothing to verify," not a
    mismatch. Raises SkillHashMismatch if the locally-computed hash
    differs from what the API resolved at enqueue time."""
    if expected_hash is None:
        return
    actual_hash = compute_skill_hash(skill_name, skills_dir=skills_dir)
    if actual_hash != expected_hash:
        raise SkillHashMismatch(
            f"skill hash mismatch for {skill_name}: job expects {expected_hash}, "
            f"local skills/ content hashes to {actual_hash} — the skill's content "
            "changed since this job was enqueued (missing required immutable "
            "snapshot match)"
        )


class SkillSnapshotMissing(RuntimeError):
    """Terminal (see noc_bridge/failures.py): a job stamped a skill_hash at
    enqueue time but no SkillSnapshot row with that content_hash exists in
    Postgres now — the immutable execution record required to run this job
    is gone or was never created. Re-running the identical job would just
    reproduce the same missing-snapshot failure, so this must never be
    treated as retryable."""


def fetch_skill_snapshot(conn, content_hash: str | None = None, snapshot_id: str | None = None) -> dict | None:
    """Reads the exact immutable SkillSnapshot row (Reliability/Phase-3
    mission: "SkillSnapshot is execution truth, not current files on
    disk") by its content_hash — the same hash the API stamped onto the
    Job/payload at enqueue time. Raw psycopg2, matching this module's
    existing hand-duplicated-schema convention (see noc_bridge/db.py) —
    the `skill_snapshots` table itself is owned by
    apps/api/app/models/models.py::SkillSnapshot."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT skill_name, version_label, content_hash, skill_md, "
            "output_schema_json, manifest_yaml FROM skill_snapshots "
            "WHERE id = %s" if snapshot_id else "WHERE content_hash = %s",
            (snapshot_id or content_hash,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def materialize_snapshot(
    conn, skill_name: str, content_hash: str | None = None, *,
    snapshot_id: str | None = None, dest_root: pathlib.Path
) -> pathlib.Path:
    """Writes the exact SkillSnapshot content identified by `content_hash`
    (fetched fresh from Postgres — never from the live, mutable `skills/`
    checkout) into `dest_root/<skill_name>/{SKILL.md, output.schema.json,
    skill.yaml}`, and returns that per-job skill directory. This is what
    makes a job reproduce Skill v3's exact behavior even if v4 was
    activated and the on-disk files rewritten before this job ever ran —
    the mission's core "Job A must run Skill v3, not v4" requirement.

    Raises SkillSnapshotMissing (terminal) if no such row exists — a
    missing/corrupted snapshot must fail safely rather than silently
    falling back to whatever happens to be on disk right now."""
    snapshot = fetch_skill_snapshot(conn, content_hash, snapshot_id=snapshot_id)
    if snapshot is None:
        raise SkillSnapshotMissing(
            f"no SkillSnapshot found for {skill_name} with snapshot_id={snapshot_id or content_hash} "
            "— cannot execute this job against an immutable snapshot that no "
            "longer exists"
        )
    if snapshot["skill_name"] != skill_name:
        raise SkillSnapshotMissing(
            f"snapshot {snapshot_id or content_hash} belongs to {snapshot['skill_name']}, not {skill_name}"
        )
    # The row is the source of truth, but its hash is still an integrity
    # guard against partial/corrupt database content.
    digest = hashlib.sha256()
    for value in (snapshot["skill_md"], snapshot["output_schema_json"], snapshot["manifest_yaml"]):
        contents = value.encode("utf-8")
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    if snapshot_id is not None and digest.hexdigest() != snapshot["content_hash"]:
        raise SkillSnapshotMissing(f"snapshot {snapshot_id or content_hash} failed content hash verification")

    skill_dir = dest_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / _PROMPT_FILENAME).write_text(snapshot["skill_md"])
    (skill_dir / _SCHEMA_FILENAME).write_text(snapshot["output_schema_json"])
    (skill_dir / _MANIFEST_FILENAME).write_text(snapshot["manifest_yaml"])

    # dest_root is normally a fresh tempfile.TemporaryDirectory, which
    # defaults to 0700 (host-user-only) — that alone blocks the sandbox's
    # fixed non-root uid (10001, §28) from even traversing into it once
    # bind-mounted read-only at /skills, regardless of skill_dir's or the
    # files' own modes. Same reasoning as
    # sandbox_runner.py::_grant_sandbox_uid_access: grant only the
    # "other" bits actually needed to read (traverse+list on the two
    # directories, read on the three files), zero "group" bits.
    dest_root.chmod(0o705)
    skill_dir.chmod(0o705)
    for filename in (_PROMPT_FILENAME, _SCHEMA_FILENAME, _MANIFEST_FILENAME):
        (skill_dir / filename).chmod(0o604)
    return dest_root
