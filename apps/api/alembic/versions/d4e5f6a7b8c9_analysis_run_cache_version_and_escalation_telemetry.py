"""analysis_run_cache_version_and_escalation_telemetry

AI cost-optimization mission, Phase 2:

- Issue 5 (cache versioning): adds `analysis_runs.cache_contract_version`,
  the combined analysis-schema/preprocessor/AI-policy version string a run
  was produced under (see app/api/v1/routers/analysis.py's
  CACHE_CONTRACT_VERSION). A cache lookup now matches on this in addition
  to skill_name/skill_version, so a schema/preprocessor/policy change
  invalidates old cache entries without a SKILL_VERSION bump.

- Issue 4 (cumulative escalation telemetry): adds `attempt_count` plus
  `initial_*` and `escalation_*` columns so a LOW->MEDIUM escalated run's
  two real Claude calls are both individually visible, not just the
  escalated call's numbers overwriting the initial attempt's. The existing
  top-level telemetry columns (input_tokens, estimated_cost_usd, etc.) now
  hold the sum of initial + escalation (see sandbox/entrypoint.py's
  run_skill), so no separate total_* column set is needed.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-12 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analysis_runs', sa.Column('cache_contract_version', sa.String(length=20), nullable=True))
    op.add_column('analysis_runs', sa.Column('attempt_count', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_model', sa.String(length=100), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_effort', sa.String(length=20), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_input_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_output_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_cache_read_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_cache_creation_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_duration_ms', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('initial_estimated_cost_usd', sa.Float(), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_model', sa.String(length=100), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_effort', sa.String(length=20), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_input_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_output_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_cache_read_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_cache_creation_tokens', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_duration_ms', sa.Integer(), nullable=True))
    op.add_column('analysis_runs', sa.Column('escalation_estimated_cost_usd', sa.Float(), nullable=True))


def downgrade() -> None:
    for col in (
        'escalation_estimated_cost_usd', 'escalation_duration_ms',
        'escalation_cache_creation_tokens', 'escalation_cache_read_tokens',
        'escalation_output_tokens', 'escalation_input_tokens',
        'escalation_effort', 'escalation_model',
        'initial_estimated_cost_usd', 'initial_duration_ms',
        'initial_cache_creation_tokens', 'initial_cache_read_tokens',
        'initial_output_tokens', 'initial_input_tokens',
        'initial_effort', 'initial_model', 'attempt_count',
        'cache_contract_version',
    ):
        op.drop_column('analysis_runs', col)
