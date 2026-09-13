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
