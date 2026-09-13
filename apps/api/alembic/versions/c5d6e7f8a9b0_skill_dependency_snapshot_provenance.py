"""Capture exact dependency snapshots on immutable skill snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_snapshots",
        sa.Column("dependency_snapshot_ids", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("skill_snapshots", "dependency_snapshot_ids")
