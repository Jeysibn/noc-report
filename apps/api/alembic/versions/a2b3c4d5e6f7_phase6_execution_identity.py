"""Add dependency-aware skill execution identity and report provenance."""
from __future__ import annotations

import hashlib
import json

import yaml
from alembic import context, op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _execution_hash(rows_by_id: dict[str, dict], row: dict, active: set[str] | None = None) -> str:
    active = set(active or ())
    if row["skill_name"] in active:
        raise RuntimeError(f"cyclic skill dependency: {row['skill_name']}")
    active.add(row["skill_name"])
    manifest = yaml.safe_load(row["manifest_yaml"]) or {}
    components = []
    pinned = row.get("dependency_snapshot_ids") or {}
    for dependency in manifest.get("dependencies", []) or []:
        name = dependency if isinstance(dependency, str) else dependency.get("id")
        child_id = pinned.get(name)
        child = rows_by_id.get(str(child_id)) if child_id else None
        if child is None:
            candidates = [
                candidate for candidate in rows_by_id.values()
                if candidate["skill_name"] == name
                and (not isinstance(dependency, dict) or dependency.get("version") is None
                     or int(candidate["version_label"]) == int(dependency["version"]))
            ]
            candidates.sort(key=lambda candidate: int(candidate["version_label"]), reverse=True)
            child = candidates[0] if candidates else None
        if child is None:
            # Preserve a deterministic identity for an old malformed row;
            # activation validation will reject it before a new job uses it.
            child_hash = hashlib.sha256(
                json.dumps({"id": name, "unresolved": True}, sort_keys=True).encode()
            ).hexdigest()
        else:
            child_hash = _execution_hash(rows_by_id, child, active)
        components.append({"id": name, "execution_hash": child_hash})
    components.sort(key=lambda item: item["id"])
    payload = {"content_hash": row["content_hash"], "dependencies": components}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def upgrade() -> None:
    # Snapshot id, not content_hash, is the immutable execution reference.
    # A skill can therefore have identical own files pinned to different
    # dependency snapshots over time.
    op.drop_constraint("skill_snapshots_content_hash_key", "skill_snapshots", type_="unique")
    op.add_column("skill_snapshots", sa.Column("execution_hash", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("skill_execution_hash", sa.String(length=64), nullable=True))
    op.add_column("analysis_runs", sa.Column("skill_execution_hash", sa.String(length=64), nullable=True))
    op.add_column("report_snapshots", sa.Column("skill_execution_hash", sa.String(length=64), nullable=True))
    op.add_column("reports", sa.Column("skill_execution_hash", sa.String(length=64), nullable=True))

    # The recursive backfill needs live row data.  Alembic's offline SQL
    # renderer cannot execute SELECTs or Python-side hashing; it still emits
    # the structural DDL, while online upgrades perform the complete
    # provenance backfill below.
    if context.is_offline_mode():
        return

    conn = op.get_bind()
    rows = [dict(row) for row in conn.execute(sa.text(
        "SELECT id, skill_name, version_label, content_hash, manifest_yaml, "
        "dependency_snapshot_ids FROM skill_snapshots"
    )).mappings()]
    rows_by_id = {str(row["id"]): row for row in rows}
    for row in rows:
        execution_hash = _execution_hash(rows_by_id, row)
        conn.execute(
            sa.text("UPDATE skill_snapshots SET execution_hash = :value WHERE id = :id"),
            {"value": execution_hash, "id": row["id"]},
        )

    conn.execute(sa.text(
        "UPDATE jobs j SET skill_execution_hash = s.execution_hash "
        "FROM skill_snapshots s WHERE j.skill_snapshot_id = s.id"
    ))
    conn.execute(sa.text(
        "UPDATE analysis_runs a SET skill_execution_hash = s.execution_hash "
        "FROM skill_snapshots s WHERE a.skill_snapshot_id = s.id"
    ))
    conn.execute(sa.text(
        "UPDATE report_snapshots r SET skill_execution_hash = s.execution_hash "
        "FROM skill_snapshots s WHERE r.skill_snapshot_id = s.id"
    ))
    conn.execute(sa.text(
        "UPDATE reports r SET skill_execution_hash = s.execution_hash "
        "FROM skill_snapshots s WHERE r.skill_snapshot_id = s.id"
    ))


def downgrade() -> None:
    op.drop_column("reports", "skill_execution_hash")
    op.drop_column("report_snapshots", "skill_execution_hash")
    op.drop_column("analysis_runs", "skill_execution_hash")
    op.drop_column("jobs", "skill_execution_hash")
    op.drop_column("skill_snapshots", "execution_hash")
    op.create_unique_constraint("skill_snapshots_content_hash_key", "skill_snapshots", ["content_hash"])
