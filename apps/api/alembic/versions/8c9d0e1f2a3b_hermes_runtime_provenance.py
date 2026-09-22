"""record provider-neutral runtime provenance on AnalysisRun

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8c9d0e1f2a3b"
down_revision: Union[str, Sequence[str], None] = "7b8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, length in (
        ("runtime_name", 50),
        ("runtime_version", 100),
        ("runtime_profile", 100),
        ("provider", 100),
        ("runtime_model", 150),
    ):
        op.add_column("analysis_runs", sa.Column(name, sa.String(length=length), nullable=True))


def downgrade() -> None:
    for name in ("runtime_model", "provider", "runtime_profile", "runtime_version", "runtime_name"):
        op.drop_column("analysis_runs", name)
