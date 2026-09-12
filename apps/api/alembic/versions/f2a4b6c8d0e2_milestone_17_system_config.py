"""milestone_17_system_config

Milestone 17 gap follow-up (Admin's "AI Configuration" tab, per the
operator's explicit scope choice: real config, live-wired to the
bridge). Single-row table — no key/value scheme, just the four known
settings.

Revision ID: f2a4b6c8d0e2
Revises: d1f2a3b4c5e6
Create Date: 2026-09-09 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f2a4b6c8d0e2'
down_revision: Union[str, Sequence[str], None] = 'd1f2a3b4c5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("default_model", sa.String(length=100), nullable=False),
        sa.Column("default_effort", sa.String(length=20), nullable=False),
        sa.Column("job_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("system_config")
