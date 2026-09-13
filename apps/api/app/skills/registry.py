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
    db: Session, skill_name: str, *, skills_dir: pathlib.Path = SKILLS_DIR
) -> SkillSnapshot:
    """Returns the immutable SkillSnapshot for the skill's *current*
    on-disk content, creating it (with the next sequential version_label
    for this skill_name) if this exact content hash hasn't been seen
    before. Existing snapshots are never mutated — a new snapshot row is
    the only way content changes get represented, by design (this is the
    Batch B analogue of the reliability mission's immutable-audit-trail
    principle applied to skills instead of jobs)."""
    content_hash = compute_skill_hash(skill_name, skills_dir=skills_dir)

    existing = db.scalar(
        select(SkillSnapshot).where(
            SkillSnapshot.skill_name == skill_name,
            SkillSnapshot.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing

    next_version = (
        db.scalar(
            select(func.coalesce(func.max(SkillSnapshot.version_label), 0)).where(
                SkillSnapshot.skill_name == skill_name
            )
        )
        or 0
    ) + 1

    # Keep "exactly one active snapshot per skill_name" true even as new
    # content is discovered on disk: the newest snapshot becomes active by
    # default (preserving pre-Phase-12 behavior of always running current
    # disk content), and an admin can later move "active" back with
    # set_active_snapshot for audit/bookkeeping purposes.
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
        is_active=True,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


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
    """Phase 12 (admin activation workflow): marks exactly one snapshot
    active per skill_name.

    This is a bookkeeping/audit control, not an override of what a job
    actually executes: the bridge always verifies the *current on-disk*
    content hash against the hash stamped on the job at enqueue time
    (`noc_bridge.skill_registry.verify_skill_hash`) — rolling "active" back
    to an older snapshot here does not change what SKILL.md content a job
    runs, and if the disk content hasn't also been reverted to match, new
    jobs will keep getting created against whatever `get_or_create_snapshot`
    finds on disk (auto-creating a fresh snapshot if it doesn't match any
    existing one). What this does control is which snapshot shows up as
    "the active one" for operators reviewing skill history/audit — e.g.
    flagging a known-bad prompt version so it's visibly not the endorsed
    one, without being able to silently rewrite what already ran.
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
