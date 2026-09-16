"""admin-configurable Claude CLI per-invocation budget

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2c3d4e5f6a7b"
down_revision: Union[str, Sequence[str], None] = "1b2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_config",
        sa.Column("claude_max_budget_usd", sa.Float(), nullable=False, server_default="0.50"),
    )
    op.alter_column("system_config", "claude_max_budget_usd", server_default=None)


def downgrade() -> None:
    op.drop_column("system_config", "claude_max_budget_usd")
