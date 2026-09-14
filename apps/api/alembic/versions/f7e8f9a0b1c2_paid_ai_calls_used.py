"""Persist actual Claude calls separately from infrastructure reservations."""
from alembic import op
import sqlalchemy as sa


revision = "f7e8f9a0b1c2"
down_revision = "f6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("paid_ai_calls_used", sa.Integer(), nullable=False, server_default="0"),
        if_not_exists=True,
    )
    op.alter_column("jobs", "paid_ai_calls_used", server_default="0")


def downgrade() -> None:
    op.drop_column("jobs", "paid_ai_calls_used")
