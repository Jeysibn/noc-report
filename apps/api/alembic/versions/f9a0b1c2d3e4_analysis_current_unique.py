"""Enforce one current AnalysisRun per incident."""
from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "f8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Repair legacy duplicates deterministically before adding the constraint.
    # The newest run remains current; historical rows are retained.
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY incident_id
                       ORDER BY created_at DESC, id DESC
                   ) AS row_number
            FROM analysis_runs
            WHERE current IS TRUE
        )
        UPDATE analysis_runs
        SET current = FALSE
        WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
    """))
    op.create_index(
        "uq_analysis_runs_current_incident",
        "analysis_runs",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("current IS TRUE"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("uq_analysis_runs_current_incident", table_name="analysis_runs", if_exists=True)
