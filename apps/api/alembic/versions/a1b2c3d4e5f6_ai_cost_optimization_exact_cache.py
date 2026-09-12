"""ai_cost_optimization_exact_cache

AI cost-optimization mission, Phase 6 (exact-match result cache):
`jobs.used_cache`/`jobs.cache_type` and `analysis_runs.used_cache`/
`analysis_runs.cache_type` record whether a log-triage-summary run reused
a prior identical-input result instead of invoking Claude again. Both
default to false/NULL so existing rows are unaffected.

Revision ID: a1b2c3d4e5f6
Revises: f2a4b6c8d0e2
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f2a4b6c8d0e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("used_cache", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs", sa.Column("cache_type", sa.String(length=20), nullable=True))
    op.add_column("analysis_runs", sa.Column("used_cache", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("analysis_runs", sa.Column("cache_type", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_runs", "cache_type")
    op.drop_column("analysis_runs", "used_cache")
    op.drop_column("jobs", "cache_type")
    op.drop_column("jobs", "used_cache")
