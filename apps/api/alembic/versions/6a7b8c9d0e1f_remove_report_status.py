"""make Job the sole Report lifecycle source

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "6a7b8c9d0e1f"
down_revision: Union[str, Sequence[str], None] = "5f6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("reports", "status")


def downgrade() -> None:
    # The historical value was only a duplicate of Job.status. Restoring the
    # column is intentionally nullable so rollback does not invent lifecycle
    # state for existing rows.
    op.add_column("reports", sa.Column("status", sa.String(length=20), nullable=True))
