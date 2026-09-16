"""harden evidence lifecycle and pin generated report artifacts

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3d4e5f6a7b8c"
down_revision: Union[str, Sequence[str], None] = "2c3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("lifecycle_state", sa.String(length=20), nullable=True))
    op.add_column("evidence", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE evidence SET lifecycle_state = 'ACTIVE' WHERE lifecycle_state IS NULL")
    op.alter_column("evidence", "lifecycle_state", nullable=False, server_default="ACTIVE")
    op.alter_column("evidence", "incident_id", nullable=True)
    op.drop_constraint("evidence_incident_id_fkey", "evidence", type_="foreignkey")
    op.create_foreign_key(
        "evidence_incident_id_fkey",
        "evidence",
        "incidents",
        ["incident_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_evidence_lifecycle_state",
        "evidence",
        "lifecycle_state IN ('ACTIVE', 'PURGE_PENDING', 'PURGED')",
    )

    op.add_column("reports", sa.Column("report_version_id", sa.String(length=200), nullable=True))
    op.add_column("reports", sa.Column("report_byte_size", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("report_content_type", sa.String(length=200), nullable=True))

    op.create_check_constraint(
        "ck_system_config_job_timeout_positive",
        "system_config",
        "job_timeout_seconds > 0",
    )
    op.create_check_constraint(
        "ck_system_config_serial_bridge_capacity",
        "system_config",
        "max_concurrent_jobs = 1",
    )
    op.create_check_constraint(
        "ck_system_config_budget_positive",
        "system_config",
        "claude_max_budget_usd > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_system_config_budget_positive", "system_config", type_="check")
    op.drop_constraint("ck_system_config_serial_bridge_capacity", "system_config", type_="check")
    op.drop_constraint("ck_system_config_job_timeout_positive", "system_config", type_="check")
    op.drop_column("reports", "report_content_type")
    op.drop_column("reports", "report_byte_size")
    op.drop_column("reports", "report_version_id")
    op.drop_constraint("ck_evidence_lifecycle_state", "evidence", type_="check")
    op.drop_constraint("evidence_incident_id_fkey", "evidence", type_="foreignkey")
    op.create_foreign_key("evidence_incident_id_fkey", "evidence", "incidents", ["incident_id"], ["id"])
    op.alter_column("evidence", "incident_id", nullable=False)
    op.drop_column("evidence", "deleted_at")
    op.drop_column("evidence", "lifecycle_state")
