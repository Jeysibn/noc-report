"""Phase 12 transactional invariants and incident prefill storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0a1b2c3d4e5f"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail loudly on pre-existing violations. Closing or rewriting operational
    # history during a schema migration would make the new invariant appear
    # healthy while concealing an incident that needs operator review.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM shifts WHERE state = 'active' GROUP BY state HAVING COUNT(*) > 1) THEN
                RAISE EXCEPTION 'cannot install uq_shifts_one_active: duplicate active shifts exist';
              END IF;
              IF EXISTS (SELECT 1 FROM reports GROUP BY shift_id, version HAVING COUNT(*) > 1) THEN
                RAISE EXCEPTION 'cannot install uq_reports_shift_version: duplicate report versions exist';
              END IF;
            END $$;
            """
        )
    )
    op.create_index(
        "uq_shifts_one_active",
        "shifts",
        ["state"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        if_not_exists=True,
    )
    op.create_unique_constraint(
        "uq_reports_shift_version",
        "reports",
        ["shift_id", "version"],
    )

    for column in (
        sa.Column("normalized_text", sa.String(), nullable=True),
        sa.Column("engine_version", sa.String(length=100), nullable=True),
        sa.Column("ocr_duration_ms", sa.Integer(), nullable=True),
        sa.Column("prefill_json", sa.JSON(), nullable=True),
        sa.Column("prefill_status", sa.String(length=30), nullable=True),
        sa.Column("prefill_model", sa.String(length=100), nullable=True),
        sa.Column("prefill_duration_ms", sa.Integer(), nullable=True),
        sa.Column("prefill_error", sa.String(), nullable=True),
    ):
        op.add_column("ocr_runs", column, if_not_exists=True)

    op.create_table(
        "incident_prefill_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_bucket", sa.String(length=100), nullable=False),
        sa.Column("source_object_key", sa.String(length=1000), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("source_mime_type", sa.String(length=200), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_ocr_text", sa.String(), nullable=True),
        sa.Column("normalized_ocr_text", sa.String(), nullable=True),
        sa.Column("ocr_engine", sa.String(length=50), nullable=False),
        sa.Column("ocr_engine_version", sa.String(length=100), nullable=True),
        sa.Column("ocr_extraction_json", sa.JSON(), nullable=False),
        sa.Column("prefill_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("prefill_model", sa.String(length=100), nullable=True),
        sa.Column("ocr_duration_ms", sa.Integer(), nullable=True),
        sa.Column("prefill_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_incident_prefill_runs_incident_id",
        "incident_prefill_runs",
        ["incident_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_incident_prefill_runs_incident_id", table_name="incident_prefill_runs", if_exists=True)
    op.drop_table("incident_prefill_runs", if_exists=True)
    for column in (
        "prefill_error",
        "prefill_duration_ms",
        "prefill_model",
        "prefill_status",
        "prefill_json",
        "ocr_duration_ms",
        "engine_version",
        "normalized_text",
    ):
        op.drop_column("ocr_runs", column)
    op.drop_constraint("uq_reports_shift_version", "reports", type_="unique")
    op.drop_index("uq_shifts_one_active", table_name="shifts", if_exists=True)
