"""Make immutable skill snapshots first-class execution references."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b4c5d6e7f8a9"
down_revision = "a3b5c7d9e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("skill_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_jobs_skill_snapshot_id", "jobs", "skill_snapshots", ["skill_snapshot_id"], ["id"])
    op.add_column("reports", sa.Column("skill_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_reports_skill_snapshot_id", "reports", "skill_snapshots", ["skill_snapshot_id"], ["id"])
    op.add_column("report_snapshots", sa.Column("skill_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_report_snapshots_skill_snapshot_id", "report_snapshots", "skill_snapshots", ["skill_snapshot_id"], ["id"])
    op.add_column("analysis_runs", sa.Column("schema_hash", sa.String(length=64), nullable=True))
    op.add_column("skill_snapshots", sa.Column("dependency_snapshot_ids", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("analysis_runs", "schema_hash")
    op.drop_column("skill_snapshots", "dependency_snapshot_ids")
    op.drop_constraint("fk_report_snapshots_skill_snapshot_id", "report_snapshots", type_="foreignkey")
    op.drop_column("report_snapshots", "skill_snapshot_id")
    op.drop_constraint("fk_reports_skill_snapshot_id", "reports", type_="foreignkey")
    op.drop_column("reports", "skill_snapshot_id")
    op.drop_constraint("fk_jobs_skill_snapshot_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "skill_snapshot_id")
