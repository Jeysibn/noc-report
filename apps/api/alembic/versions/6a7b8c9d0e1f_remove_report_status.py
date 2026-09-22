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
    # This historical branch can reach this revision before the reports table
    # is created on a fresh database. Defer the removal until the table exists
    # rather than making a clean migration impossible.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("reports"):
        columns = {column["name"] for column in inspector.get_columns("reports")}
        if "status" in columns:
            op.drop_column("reports", "status")


def downgrade() -> None:
    # The historical value was only a duplicate of Job.status. Restoring the
    # column is intentionally nullable so rollback does not invent lifecycle
    # state for existing rows.
    op.add_column("reports", sa.Column("status", sa.String(length=20), nullable=True))
