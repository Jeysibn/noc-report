"""Skill Registry — runtime-support mirror (Reliability mission Batch B).

Mirrors `apps/api/app/skills/registry.py::compute_skill_hash` for legacy
hash-only callers. Snapshot-backed jobs use the immutable PostgreSQL row
instead: runtime support verifies its captured SKILL.md, output.schema.json, and
skill.yaml bytes plus the dependency-aware execution identity, then
materializes only those bytes into a job workspace. The
live `skills/` checkout is therefore not part of the execution path for a
current job; hash verification against that checkout remains only as a
compatibility path for old messages that carry no snapshot reference.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import yaml

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
            "SELECT id, skill_name, version_label, content_hash, skill_md, "
            "output_schema_json, manifest_yaml, dependency_snapshot_ids, execution_hash FROM skill_snapshots "
            "WHERE id = %s" if snapshot_id else "WHERE content_hash = %s ORDER BY created_at DESC LIMIT 1",
            (snapshot_id or content_hash,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_dependency_snapshot(conn, dependency) -> dict | None:
    name = dependency if isinstance(dependency, str) else dependency.get("id")
    version = None if isinstance(dependency, str) else dependency.get("version")
    query = "SELECT id, skill_name, version_label, content_hash, skill_md, output_schema_json, manifest_yaml, dependency_snapshot_ids, execution_hash FROM skill_snapshots WHERE skill_name = %s"
    params = [name]
    if version is not None:
        query += " AND version_label = %s"
        params.append(int(version))
    query += " ORDER BY version_label DESC LIMIT 1"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, tuple(params))
        row = cur.fetchone()
    return dict(row) if row else None


def _verify_snapshot_row(snapshot: dict, *, expected_hash: str | None = None) -> None:
    """Verify the immutable bytes represented by one database row."""
    actual = hashlib.sha256()
    for value in (snapshot["skill_md"], snapshot["output_schema_json"], snapshot["manifest_yaml"]):
        contents = value.encode("utf-8")
        actual.update(len(contents).to_bytes(8, "big"))
        actual.update(contents)
    actual_hash = actual.hexdigest()
    # Pre-registry fixtures used short sentinel hashes and cannot provide a
    # cryptographic integrity claim. Real snapshots are SHA-256 values and
    # are always checked.
    if len(snapshot["content_hash"]) == 64 and actual_hash != snapshot["content_hash"]:
        raise SkillSnapshotMissing(f"snapshot {snapshot.get('id') or snapshot['content_hash']} failed content hash verification")
    if expected_hash is not None and expected_hash != snapshot["content_hash"]:
        raise SkillSnapshotMissing(
            f"snapshot {snapshot.get('id') or expected_hash} hash does not match the job reference"
        )


def _execution_hash(conn, snapshot: dict, active_names: set[str] | None = None) -> str:
    active_names = set(active_names or ())
    if snapshot["skill_name"] in active_names:
        raise SkillSnapshotMissing(f"cyclic skill dependency: {snapshot['skill_name']}")
    active_names.add(snapshot["skill_name"])
    manifest = yaml.safe_load(snapshot["manifest_yaml"]) or {}
    components = []
    pinned = snapshot.get("dependency_snapshot_ids") or {}
    for dependency in manifest.get("dependencies", []) or []:
        name = dependency if isinstance(dependency, str) else dependency.get("id")
        child = fetch_skill_snapshot(conn, snapshot_id=pinned.get(name)) if pinned.get(name) else fetch_dependency_snapshot(conn, dependency)
        if child is None or child["skill_name"] != name:
            raise SkillSnapshotMissing(f"dependency snapshot is missing: {dependency!r}")
        components.append({
            "id": name,
            "content_hash": child["content_hash"],
            "execution_hash": _execution_hash(conn, child, active_names),
        })
    components.sort(key=lambda item: (item["id"], item["content_hash"]))
    payload = {"content_hash": snapshot["content_hash"], "dependencies": components}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_snapshot_files(snapshot: dict, dest_root: pathlib.Path) -> pathlib.Path:
    skill_dir = dest_root / snapshot["skill_name"]
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / _PROMPT_FILENAME).write_text(snapshot["skill_md"])
    (skill_dir / _SCHEMA_FILENAME).write_text(snapshot["output_schema_json"])
    (skill_dir / _MANIFEST_FILENAME).write_text(snapshot["manifest_yaml"])
    skill_dir.chmod(0o705)
    for filename in (_PROMPT_FILENAME, _SCHEMA_FILENAME, _MANIFEST_FILENAME):
        (skill_dir / filename).chmod(0o604)
    return skill_dir


def _materialize_snapshot_tree(
    conn,
    snapshot: dict,
    *,
    dest_root: pathlib.Path,
    active_names: set[str],
    materialized_ids: set[str],
    materialized_names: dict[str, str],
    expected_execution_hash: str | None = None,
) -> None:
    """Materialize one snapshot and every exact transitive dependency."""
    snapshot_id = str(snapshot.get("id") or snapshot["content_hash"])
    skill_name = snapshot["skill_name"]
    if skill_name in active_names:
        raise SkillSnapshotMissing(f"cyclic skill dependency: {skill_name}")
    previous_id = materialized_names.get(skill_name)
    if previous_id is not None and previous_id != snapshot_id:
        raise SkillSnapshotMissing(
            f"dependency name {skill_name} resolves to multiple snapshots: "
            f"{previous_id} and {snapshot_id}"
        )
    if snapshot_id in materialized_ids:
        return

    _verify_snapshot_row(snapshot)
    if expected_execution_hash is not None and _execution_hash(conn, snapshot) != expected_execution_hash:
        raise SkillSnapshotMissing(
            f"snapshot {snapshot_id} execution hash does not match the job reference"
        )
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_root.chmod(0o705)
    _write_snapshot_files(snapshot, dest_root)
    materialized_ids.add(snapshot_id)
    materialized_names[skill_name] = snapshot_id

    manifest = yaml.safe_load(snapshot["manifest_yaml"]) or {}
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SkillSnapshotMissing(f"dependencies for {skill_name} must be a list")
    dependency_ids = snapshot.get("dependency_snapshot_ids") or {}
    for dependency in dependencies:
        dependency_name = dependency if isinstance(dependency, str) else dependency.get("id")
        if not dependency_name:
            raise SkillSnapshotMissing(f"dependency is missing an id: {dependency!r}")
        dep = (
            fetch_skill_snapshot(conn, snapshot_id=dependency_ids.get(dependency_name))
            if dependency_ids.get(dependency_name)
            else fetch_dependency_snapshot(conn, dependency)
        )
        if dep is None:
            raise SkillSnapshotMissing(f"dependency snapshot is missing: {dependency}")
        if dep["skill_name"] != dependency_name:
            raise SkillSnapshotMissing(
                f"dependency snapshot {dep['skill_name']} does not satisfy {dependency_name}"
            )
        if isinstance(dependency, dict) and dependency.get("version") is not None:
            try:
                requested_version = int(dependency["version"])
            except (TypeError, ValueError) as exc:
                raise SkillSnapshotMissing(f"invalid dependency version: {dependency}") from exc
            if int(dep["version_label"]) != requested_version:
                raise SkillSnapshotMissing(f"dependency snapshot version does not satisfy {dependency}")
        _materialize_snapshot_tree(
            conn,
            dep,
            dest_root=dest_root,
            active_names=active_names | {skill_name},
            materialized_ids=materialized_ids,
            materialized_names=materialized_names,
        )


def materialize_snapshot(
    conn, skill_name: str, content_hash: str | None = None, *,
    snapshot_id: str | None = None, execution_hash: str | None = None,
    dest_root: pathlib.Path
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
    # The row is the source of truth. Verify and materialize its complete
    # immutable dependency graph; no live checkout is involved.
    _verify_snapshot_row(snapshot, expected_hash=content_hash)
    _materialize_snapshot_tree(
        conn,
        snapshot,
        dest_root=dest_root,
        active_names=set(),
        materialized_ids=set(),
        materialized_names={},
        expected_execution_hash=execution_hash,
    )
    return dest_root
