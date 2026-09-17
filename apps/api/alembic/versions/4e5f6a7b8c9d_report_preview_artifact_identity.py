"""pin structured ReportDocument preview artifacts

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4e5f6a7b8c9d"
down_revision: Union[str, Sequence[str], None] = "3d4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("document_version_id", sa.String(length=200), nullable=True))
    op.add_column("reports", sa.Column("document_sha256", sa.String(length=64), nullable=True))
    op.add_column("reports", sa.Column("document_byte_size", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("document_content_type", sa.String(length=200), nullable=True))
    op.add_column("reports", sa.Column("screenshots_version_id", sa.String(length=200), nullable=True))
    op.add_column("reports", sa.Column("screenshots_sha256", sa.String(length=64), nullable=True))
    op.add_column("reports", sa.Column("screenshots_byte_size", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("screenshots_content_type", sa.String(length=200), nullable=True))


def downgrade() -> None:
    for name in (
        "screenshots_content_type", "screenshots_byte_size", "screenshots_sha256", "screenshots_version_id",
        "document_content_type", "document_byte_size", "document_sha256", "document_version_id",
    ):
        op.drop_column("reports", name)
