"""Persist uncached plus prompt-cache input token accounting."""
from alembic import op
import sqlalchemy as sa


revision = "f4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("total_model_input_tokens", sa.Integer(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    op.drop_column("analysis_runs", "total_model_input_tokens")
