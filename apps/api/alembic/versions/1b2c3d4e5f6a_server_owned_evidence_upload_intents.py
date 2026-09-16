"""server-owned evidence upload intents and immutable completion metadata

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "1b2c3d4e5f6a"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_upload_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(length=100), nullable=False),
        sa.Column("object_key", sa.String(length=1000), nullable=False),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("expected_content_type", sa.String(length=200), nullable=True),
        sa.Column("expected_byte_size", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket", "object_key", name="uq_evidence_upload_intent_object"),
        sa.UniqueConstraint("evidence_id"),
    )
    op.create_index(
        "ix_evidence_upload_intents_incident_id",
        "evidence_upload_intents",
        ["incident_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_upload_intents_incident_id", table_name="evidence_upload_intents")
    op.drop_table("evidence_upload_intents")
