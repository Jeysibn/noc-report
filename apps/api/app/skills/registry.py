"""Skill Registry (Reliability mission Batch B, Phases 4-9).

Before this, a skill's identity was two free-text strings
(`skill_name`/`skill_version`) threaded through job messages and
`AnalysisRun`/`Report` rows — nothing stopped the actual prompt or output
contract behind a given "version" from silently drifting (edit
SKILL.md, forget to bump SKILL_VERSION, and every downstream consumer —
the cache, the audit trail, a redelivered job — silently starts
reasoning about a different prompt under the same label).

This module makes a skill's *content* — its `SKILL.md` prompt,
`output.schema.json` contract, and `skill.yaml` manifest — the actual
identity: `compute_skill_hash` hashes those three files' bytes, and
`get_or_create_snapshot` records an immutable `SkillSnapshot` row the
first time a given hash is seen for a skill name, auto-incrementing a
human-readable version label. Editing any of the three files produces a
new hash, therefore a new snapshot, therefore an automatic cache miss and
a new audit trail entry — no manual version bump required to get that
safety, though `skill_version` (the human-chosen label elsewhere in the
code) is kept too since it's still useful as a stable display name.

`bridge/noc_bridge/skill_registry.py` mirrors this module's hashing logic
(same separate-deployable-kept-in-sync-by-hand convention as
`noc_bridge/db.py`/`noc_bridge/config.py`) so the bridge can verify, right
before executing a job, that the skill content it's about to run still
matches the hash the API computed at enqueue time.
"""
from __future__ import annotations

import hashlib
import pathlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import SkillSnapshot

# apps/api/app/skills/registry.py -> apps/api/app -> apps/api -> apps -> repo root
SKILLS_DIR = pathlib.Path(__file__).resolve().parents[4] / "skills"

_MANIFEST_FILENAME = "skill.yaml"
_PROMPT_FILENAME = "SKILL.md"
_SCHEMA_FILENAME = "output.schema.json"


class SkillFilesMissing(RuntimeError):
    """Raised when a skill's on-disk files can't be found/read at all —
    a fundamentally different problem from a hash mismatch, and one that
    should never be silently swallowed into "just re-hash whatever's
    there"."""


def skill_dir(skill_name: str, *, skills_dir: pathlib.Path = SKILLS_DIR) -> pathlib.Path:
    return skills_dir / skill_name


def compute_skill_hash(skill_name: str, *, skills_dir: pathlib.Path = SKILLS_DIR) -> str:
    """sha256 over the three identity files' bytes, in a fixed order, each
    length-prefixed so e.g. an empty schema file can't be confused with a
    missing one and so concatenation is unambiguous."""
    directory = skill_dir(skill_name, skills_dir=skills_dir)
    digest = hashlib.sha256()
    for filename in (_PROMPT_FILENAME, _SCHEMA_FILENAME, _MANIFEST_FILENAME):
        path = directory / filename
        try:
            contents = path.read_bytes()
        except FileNotFoundError as exc:
            raise SkillFilesMissing(f"{skill_name}: missing {filename} at {path}") from exc
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def read_skill_files(skill_name: str, *, skills_dir: pathlib.Path = SKILLS_DIR) -> dict[str, str]:
    directory = skill_dir(skill_name, skills_dir=skills_dir)
    return {
        "skill_md": (directory / _PROMPT_FILENAME).read_text(encoding="utf-8"),
        "output_schema_json": (directory / _SCHEMA_FILENAME).read_text(encoding="utf-8"),
        "manifest_yaml": (directory / _MANIFEST_FILENAME).read_text(encoding="utf-8"),
    }


def get_or_create_snapshot(
    db: Session,
    skill_name: str,
    *,
    skills_dir: pathlib.Path = SKILLS_DIR,
    activate: bool | None = None,
) -> SkillSnapshot:
    """Returns the immutable SkillSnapshot for the skill's *current*
    on-disk content, creating it (with the next sequential version_label
    for this skill_name) if this exact content hash hasn't been seen
    before. Existing snapshots are never mutated — a new snapshot row is
    the only way content changes get represented, by design (this is the
    Batch B analogue of the reliability mission's immutable-audit-trail
    principle applied to skills instead of jobs).

    Skill Runtime mission Phase 2: a newly-discovered content hash is
    registered as a Draft (not auto-activated) unless `activate=True` is
    passed explicitly, or this is the very first snapshot ever seen for
    this skill_name (bootstrap — there must always be exactly one active
    snapshot once a skill has any history at all). This is what makes
    editing SKILL.md on disk *not* silently change what new jobs execute —
    see `resolve_active_snapshot`, the function that actually controls job
    dispatch; an admin must call `set_active_snapshot` (or pass
    `activate=True` here) to promote a Draft to Active."""
    content_hash = compute_skill_hash(skill_name, skills_dir=skills_dir)

    existing = db.scalar(
        select(SkillSnapshot).where(
            SkillSnapshot.skill_name == skill_name,
            SkillSnapshot.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing

    is_first_snapshot_for_skill = (
        db.scalar(
            select(func.count()).select_from(SkillSnapshot).where(SkillSnapshot.skill_name == skill_name)
        )
        or 0
    ) == 0

    next_version = (
        db.scalar(
            select(func.coalesce(func.max(SkillSnapshot.version_label), 0)).where(
                SkillSnapshot.skill_name == skill_name
            )
        )
        or 0
    ) + 1

    should_activate = activate if activate is not None else is_first_snapshot_for_skill
    if should_activate:
        for other in db.scalars(select(SkillSnapshot).where(SkillSnapshot.skill_name == skill_name)):
            other.is_active = False

    files = read_skill_files(skill_name, skills_dir=skills_dir)
    snapshot = SkillSnapshot(
        skill_name=skill_name,
        version_label=next_version,
        content_hash=content_hash,
        skill_md=files["skill_md"],
        output_schema_json=files["output_schema_json"],
        manifest_yaml=files["manifest_yaml"],
        is_active=should_activate,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


class NoActiveSkillSnapshot(RuntimeError):
    """Raised by resolve_active_snapshot when a skill_name has snapshot
    history but somehow none of them is flagged active — should not
    normally happen (bootstrap always activates the first snapshot), but
    must fail loudly rather than silently guessing one."""


def resolve_active_snapshot(
    db: Session, skill_name: str, *, skills_dir: pathlib.Path = SKILLS_DIR
) -> SkillSnapshot:
    """Skill Runtime mission Phase 2: the actual execution-controlling
    lookup a job-creation endpoint must call (not `get_or_create_snapshot`
    directly). Registers any new on-disk content as a Draft snapshot for
    audit/history visibility (same content-hash provenance as before), but
    a new job is stamped with whichever snapshot is_active=True — set by
    an operator's explicit `set_active_snapshot` call, not by whatever
    happens to be on disk right now. This is what makes activation real:
    activating v2 makes the *next* job use v2 regardless of what v3/v4
    on-disk edits have happened since."""
    get_or_create_snapshot(db, skill_name, skills_dir=skills_dir)
    active = db.scalar(
        select(SkillSnapshot).where(
            SkillSnapshot.skill_name == skill_name,
            SkillSnapshot.is_active.is_(True),
        )
    )
    if active is None:
        raise NoActiveSkillSnapshot(f"{skill_name}: has snapshot history but no active snapshot")
    return active


class SkillVersionNotFound(RuntimeError):
    """Raised by set_active_snapshot when no snapshot with the requested
    version_label exists for that skill_name."""


def list_snapshots(db: Session, skill_name: str) -> list[SkillSnapshot]:
    """Every snapshot ever recorded for a skill, newest first — the
    version history an admin picks a rollback target from."""
    return list(
        db.scalars(
            select(SkillSnapshot)
            .where(SkillSnapshot.skill_name == skill_name)
            .order_by(SkillSnapshot.version_label.desc())
        )
    )


def list_skill_names(db: Session) -> list[str]:
    return sorted(
        {row for row in db.scalars(select(SkillSnapshot.skill_name).distinct())}
    )


def set_active_snapshot(db: Session, skill_name: str, version_label: int) -> SkillSnapshot:
    """Phase 12 (admin activation workflow), superseded by Skill Runtime
    mission Phase 2: marks exactly one snapshot active per skill_name.

    This now genuinely controls what *new* jobs execute: job-creation
    endpoints resolve the skill to run via `resolve_active_snapshot`, which
    reads `is_active`, and the bridge materializes and executes that exact
    snapshot's frozen content (`bridge/noc_bridge/skill_registry.
    materialize_snapshot`) — never the live on-disk files. Activating an
    older version_label is therefore a real, working rollback: the very
    next job created for this skill_name runs that snapshot's exact
    content, with no need to revert any files on disk. Jobs already
    created/queued before this call are unaffected — they were stamped
    with a specific `skill_hash` at enqueue time and remain reproducible
    against that snapshot regardless of what's active now.
    """
    target = db.scalar(
        select(SkillSnapshot).where(
            SkillSnapshot.skill_name == skill_name,
            SkillSnapshot.version_label == version_label,
        )
    )
    if target is None:
        raise SkillVersionNotFound(f"{skill_name}: no snapshot with version_label={version_label}")

    others = db.scalars(
        select(SkillSnapshot).where(
            SkillSnapshot.skill_name == skill_name,
            SkillSnapshot.id != target.id,
        )
    )
    for snapshot in others:
        snapshot.is_active = False
    target.is_active = True
    db.flush()
    return target
