"""Ensure direct bridge/test inserts receive the Job AI budget defaults."""
from alembic import op


revision = "f6d7e8f9a0b1"
down_revision = "f5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("jobs", "paid_ai_call_budget", server_default="4")
    op.alter_column("jobs", "paid_ai_calls_reserved", server_default="0")


def downgrade() -> None:
    op.alter_column("jobs", "paid_ai_call_budget", server_default=None)
    op.alter_column("jobs", "paid_ai_calls_reserved", server_default=None)
