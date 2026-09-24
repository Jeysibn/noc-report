"""retire database-backed worker controls

Revision ID: a0b1c2d3e4f5
Revises: 9d0e1f2a3b4c

Worker timeout and serial execution were never read by the worker runtime.
The deployment environment is the authoritative owner, so retire this
misleading single-row control table. No job, evidence, or report data is
stored in it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "9d0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("system_config"):
        op.drop_table("system_config")


def downgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("job_timeout_seconds > 0", name="ck_system_config_job_timeout_positive"),
        sa.CheckConstraint("max_concurrent_jobs = 1", name="ck_system_config_serial_worker_capacity"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "system_config",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("job_timeout_seconds", sa.Integer()),
            sa.column("max_concurrent_jobs", sa.Integer()),
        ),
        [{"id": "00000000-0000-0000-0000-000000000001", "job_timeout_seconds": 300, "max_concurrent_jobs": 1}],
    )
