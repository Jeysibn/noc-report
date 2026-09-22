"""store explicit structured preview object keys

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5f6a7b8c9d0e"
down_revision: Union[str, Sequence[str], None] = "4e5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("reports"):
        op.add_column("reports", sa.Column("document_object_key", sa.String(length=1000), nullable=True))
        op.add_column("reports", sa.Column("screenshots_object_key", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("reports"):
        return
    op.drop_column("reports", "screenshots_object_key")
    op.drop_column("reports", "document_object_key")
