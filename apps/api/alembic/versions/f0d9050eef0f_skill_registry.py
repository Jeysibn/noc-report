"""Skill Registry: skill_snapshots table + skill_hash columns

Reliability mission Batch B (Phases 4-9): an immutable, content-hashed
SkillSnapshot table replaces bare skill_name/skill_version free-text
labels as the real identity of a skill version. jobs/analysis_runs/
reports gain a skill_hash column recording which snapshot was actually
used.

Revision ID: f0d9050eef0f
Revises: e5f6a7b8c9d0
Create Date: 2026-09-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f0d9050eef0f'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'skill_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('skill_name', sa.String(length=100), nullable=False),
        sa.Column('version_label', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('skill_md', sa.String(), nullable=False),
        sa.Column('output_schema_json', sa.String(), nullable=False),
        sa.Column('manifest_yaml', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index('ix_skill_snapshots_skill_name', 'skill_snapshots', ['skill_name'])

    op.add_column('jobs', sa.Column('skill_hash', sa.String(length=64), nullable=True))
    op.add_column('analysis_runs', sa.Column('skill_hash', sa.String(length=64), nullable=True))
    op.add_column('reports', sa.Column('skill_hash', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'skill_hash')
    op.drop_column('analysis_runs', 'skill_hash')
    op.drop_column('jobs', 'skill_hash')
    op.drop_index('ix_skill_snapshots_skill_name', table_name='skill_snapshots')
    op.drop_table('skill_snapshots')
