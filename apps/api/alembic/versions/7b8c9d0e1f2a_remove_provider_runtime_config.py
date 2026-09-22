"""remove legacy provider runtime configuration

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f

Earlier revisions remain immutable migration history. This forward
migration removes their provider-specific active settings while retaining
generic worker limits and all job/report provenance columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7b8c9d0e1f2a"
down_revision: Union[str, Sequence[str], None] = "6a7b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("system_config"):
        columns = {column["name"] for column in inspector.get_columns("system_config")}
        constraints = {constraint.get("name") for constraint in inspector.get_check_constraints("system_config")}
        if "ck_system_config_budget_positive" in constraints:
            op.drop_constraint("ck_system_config_budget_positive", "system_config", type_="check")
        for name in ("claude_max_budget_usd", "default_effort", "default_model"):
            if name in columns:
                op.drop_column("system_config", name)
        if "ck_system_config_serial_bridge_capacity" in constraints:
            op.drop_constraint("ck_system_config_serial_bridge_capacity", "system_config", type_="check")
        if "ck_system_config_serial_worker_capacity" not in constraints:
            op.create_check_constraint(
                "ck_system_config_serial_worker_capacity",
                "system_config",
                "max_concurrent_jobs = 1",
            )

    # Older report-artifact revisions sit before the historical report-table
    # creation revision on one branch. Fresh databases therefore arrive here
    # without those additive columns; deployed databases already have them.
    if sa.inspect(op.get_bind()).has_table("reports"):
        existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("reports")}
        columns = {
            "report_version_id": sa.Column("report_version_id", sa.String(length=200), nullable=True),
            "report_byte_size": sa.Column("report_byte_size", sa.Integer(), nullable=True),
            "report_content_type": sa.Column("report_content_type", sa.String(length=200), nullable=True),
            "document_version_id": sa.Column("document_version_id", sa.String(length=200), nullable=True),
            "document_sha256": sa.Column("document_sha256", sa.String(length=64), nullable=True),
            "document_byte_size": sa.Column("document_byte_size", sa.Integer(), nullable=True),
            "document_content_type": sa.Column("document_content_type", sa.String(length=200), nullable=True),
            "screenshots_version_id": sa.Column("screenshots_version_id", sa.String(length=200), nullable=True),
            "screenshots_sha256": sa.Column("screenshots_sha256", sa.String(length=64), nullable=True),
            "screenshots_byte_size": sa.Column("screenshots_byte_size", sa.Integer(), nullable=True),
            "screenshots_content_type": sa.Column("screenshots_content_type", sa.String(length=200), nullable=True),
            "document_object_key": sa.Column("document_object_key", sa.String(length=1000), nullable=True),
            "screenshots_object_key": sa.Column("screenshots_object_key", sa.String(length=1000), nullable=True),
        }
        for name, column in columns.items():
            if name not in existing:
                op.add_column("reports", column)


def downgrade() -> None:
    op.drop_constraint("ck_system_config_serial_worker_capacity", "system_config", type_="check")
    op.create_check_constraint(
        "ck_system_config_serial_bridge_capacity",
        "system_config",
        "max_concurrent_jobs = 1",
    )
    op.add_column(
        "system_config",
        sa.Column("default_model", sa.String(length=100), nullable=False, server_default="retired"),
    )
    op.add_column(
        "system_config",
        sa.Column("default_effort", sa.String(length=20), nullable=False, server_default="low"),
    )
    op.add_column(
        "system_config",
        sa.Column("claude_max_budget_usd", sa.Float(), nullable=False, server_default="0.50"),
    )
    op.create_check_constraint(
        "ck_system_config_budget_positive",
        "system_config",
        "claude_max_budget_usd > 0",
    )
