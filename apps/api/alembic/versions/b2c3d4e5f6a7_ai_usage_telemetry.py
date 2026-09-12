"""ai_usage_telemetry

AI cost-optimization mission, Phase 1 (AI usage telemetry): persists the
per-run usage fields sandbox/entrypoint.py already computes (and, until
now, only printed to stderr) onto `analysis_runs`. All nullable — a
missing/unavailable field (older run, sandbox image without telemetry.json,
upload failure) must never block anything.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("cache_creation_tokens", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("estimated_cost_usd", sa.Float(), nullable=True))
    op.add_column("analysis_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("num_turns", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("analysis_runs", sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("analysis_runs", sa.Column("escalation_reason", sa.String(length=500), nullable=True))
    op.add_column("analysis_runs", sa.Column("raw_input_bytes", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("evidence_bytes", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("preprocessing_ratio", sa.Float(), nullable=True))


def downgrade() -> None:
    for col in (
        "preprocessing_ratio", "evidence_bytes", "raw_input_bytes", "escalation_reason",
        "escalated", "confidence", "num_turns", "duration_ms", "estimated_cost_usd",
        "cache_read_tokens", "cache_creation_tokens", "output_tokens", "input_tokens",
    ):
        op.drop_column("analysis_runs", col)
