"""record provider-neutral runtime provenance on Report

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9d0e1f2a3b4c"
down_revision: Union[str, Sequence[str], None] = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("runtime_name", sa.Column("runtime_name", sa.String(length=50), nullable=True)),
        ("runtime_version", sa.Column("runtime_version", sa.String(length=100), nullable=True)),
        ("runtime_profile", sa.Column("runtime_profile", sa.String(length=100), nullable=True)),
        ("provider", sa.Column("provider", sa.String(length=100), nullable=True)),
        ("runtime_model", sa.Column("runtime_model", sa.String(length=150), nullable=True)),
        ("input_manifest_sha256", sa.Column("input_manifest_sha256", sa.String(length=64), nullable=True)),
        ("output_sha256", sa.Column("output_sha256", sa.String(length=64), nullable=True)),
        ("input_tokens", sa.Column("input_tokens", sa.Integer(), nullable=True)),
        ("output_tokens", sa.Column("output_tokens", sa.Integer(), nullable=True)),
        ("duration_ms", sa.Column("duration_ms", sa.Integer(), nullable=True)),
    ):
        op.add_column("reports", column)


def downgrade() -> None:
    for name in (
        "duration_ms", "output_tokens", "input_tokens", "output_sha256",
        "input_manifest_sha256", "runtime_model", "provider", "runtime_profile",
        "runtime_version", "runtime_name",
    ):
        op.drop_column("reports", name)
