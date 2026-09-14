"""Persist a bounded paid Claude call budget per Job."""
from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = ("a2b3c4d5e6f7", "f2a4b6c8d0e2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("paid_ai_call_budget", sa.Integer(), nullable=False, server_default="4"),
        if_not_exists=True,
    )
    op.add_column(
        "jobs",
        sa.Column("paid_ai_calls_reserved", sa.Integer(), nullable=False, server_default="0"),
        if_not_exists=True,
    )
    op.alter_column("jobs", "paid_ai_call_budget", server_default="4")
    op.alter_column("jobs", "paid_ai_calls_reserved", server_default="0")


def downgrade() -> None:
    op.drop_column("jobs", "paid_ai_calls_reserved")
    op.drop_column("jobs", "paid_ai_call_budget")
