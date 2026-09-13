"""AnalysisRun: skill_snapshot_id provenance FK

Skill Runtime mission Phase 6: skill_hash alone identifies *which*
snapshot content produced a run, but reading it back required a second
lookup by content_hash. analysis_runs.skill_snapshot_id is a direct
foreign key to the exact skill_snapshots row used, so an AnalysisRun's
full provenance (SKILL.md prose, output schema, manifest, activation
history) is one join away instead of a hash-keyed re-query.

Revision ID: a3b5c7d9e1f2
Revises: f0d9050eef0f
Create Date: 2026-09-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a3b5c7d9e1f2'
down_revision = 'f0d9050eef0f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'analysis_runs',
        sa.Column('skill_snapshot_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_analysis_runs_skill_snapshot_id',
        'analysis_runs', 'skill_snapshots',
        ['skill_snapshot_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_analysis_runs_skill_snapshot_id', 'analysis_runs', type_='foreignkey')
    op.drop_column('analysis_runs', 'skill_snapshot_id')
