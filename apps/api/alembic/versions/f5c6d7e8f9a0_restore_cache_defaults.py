"""Keep raw bridge inserts compatible with non-null cache flags."""
from alembic import op
import sqlalchemy as sa


revision = "f5c6d7e8f9a0"
down_revision = "f4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE jobs SET used_cache = FALSE WHERE used_cache IS NULL")
    op.execute("UPDATE analysis_runs SET used_cache = FALSE WHERE used_cache IS NULL")
    op.alter_column("jobs", "used_cache", server_default=sa.false())
    op.alter_column("analysis_runs", "used_cache", server_default=sa.false())
    op.alter_column("jobs", "paid_ai_call_budget", server_default="4")
    op.alter_column("jobs", "paid_ai_calls_reserved", server_default="0")


def downgrade() -> None:
    op.alter_column("jobs", "used_cache", server_default=None)
    op.alter_column("analysis_runs", "used_cache", server_default=None)
    op.alter_column("jobs", "paid_ai_call_budget", server_default=None)
    op.alter_column("jobs", "paid_ai_calls_reserved", server_default=None)
