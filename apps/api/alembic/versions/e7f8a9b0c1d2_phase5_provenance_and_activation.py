"""Phase 5 provenance columns and one-active-snapshot invariant.

The runtime model now records the contract axes needed for cache and report
traceability, while PostgreSQL enforces one active snapshot per skill.
"""
from alembic import op
import sqlalchemy as sa


revision = "e7f8a9b0c1d2"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("input_contract_version", sa.String(length=100), nullable=True))
    op.add_column("analysis_runs", sa.Column("preprocessor_version", sa.String(length=50), nullable=True))
    op.add_column("analysis_runs", sa.Column("ai_policy_version", sa.String(length=50), nullable=True))
    op.add_column("analysis_runs", sa.Column("ai_policy_json", sa.JSON(), nullable=True))
    # Normalize any pre-index database that was populated before the
    # activation invariant was database-enforced. Keep the newest snapshot
    # active for each logical skill; historical rows remain available for
    # rollback.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY skill_name
                       ORDER BY created_at DESC NULLS LAST, version_label DESC, id DESC
                   ) AS rank
            FROM skill_snapshots
            WHERE is_active IS TRUE
        )
        UPDATE skill_snapshots AS s
        SET is_active = FALSE
        FROM ranked AS r
        WHERE s.id = r.id AND r.rank > 1
        """
    )
    op.create_index(
        "uq_skill_snapshots_one_active_per_name",
        "skill_snapshots",
        ["skill_name"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_skill_snapshots_one_active_per_name", table_name="skill_snapshots")
    op.drop_column("analysis_runs", "ai_policy_json")
    op.drop_column("analysis_runs", "ai_policy_version")
    op.drop_column("analysis_runs", "preprocessor_version")
    op.drop_column("analysis_runs", "input_contract_version")
