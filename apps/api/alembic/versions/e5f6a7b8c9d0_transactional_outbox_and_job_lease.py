"""transactional_outbox_and_job_lease

Reliability mission Batch A (durable dispatch + idempotent job lifecycle):

- `outbox_events`: the transactional outbox. A row is created in the same
  transaction as the Job (and AnalysisRun/Report/ReportSnapshot) it
  announces, so RabbitMQ is only ever published to after that domain
  state is durably committed (see app/outbox.py). `published_at IS NULL`
  marks a row as still-due; the dispatcher retries those.

- `jobs.claimed_at` / `claim_token` / `lease_expires_at` / `worker_id`:
  a claim/lease so the bridge's idempotent job lifecycle module can tell
  whether a redelivered message is still legitimately in flight elsewhere
  or safe (and necessary) to (re-)execute, instead of relying purely on
  RabbitMQ's at-least-once redelivery plus in-memory state.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('claim_token', sa.String(length=64), nullable=True))
    op.add_column('jobs', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('worker_id', sa.String(length=200), nullable=True))

    op.create_table(
        'outbox_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('aggregate_type', sa.String(length=50), nullable=False),
        sa.Column('aggregate_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('routing_key', sa.String(length=200), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.String(), nullable=True),
    )
    op.create_index('ix_outbox_events_unpublished', 'outbox_events', ['published_at', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_outbox_events_unpublished', table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_column('jobs', 'worker_id')
    op.drop_column('jobs', 'lease_expires_at')
    op.drop_column('jobs', 'claim_token')
    op.drop_column('jobs', 'claimed_at')
