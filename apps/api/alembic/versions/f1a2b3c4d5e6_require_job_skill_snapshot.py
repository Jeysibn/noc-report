"""Require immutable skill provenance for executable jobs.

Existing rows are backfilled from their immutable content hash. If an old
analysis/report job cannot be mapped to a SkillSnapshot, the migration fails
instead of allowing an untraceable job to remain executable. The Job column
is then made non-null, making the immutable execution contract universal for
all current and future executable Job rows.
"""
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE jobs AS j
        SET skill_snapshot_id = s.id
        FROM skill_snapshots AS s
        WHERE j.skill_snapshot_id IS NULL
          AND j.skill_hash IS NOT NULL
          AND s.content_hash = j.skill_hash
        """
    )
    # Older producers may have persisted only the human-readable skill name
    # and numeric registry label. Use that as a migration fallback, but never
    # use it as the runtime identity after this constraint is installed.
    op.execute(
        """
        UPDATE jobs AS j
        SET skill_snapshot_id = s.id
        FROM skill_snapshots AS s
        WHERE j.skill_snapshot_id IS NULL
          AND j.skill_hash IS NULL
          AND j.skill_name IS NOT NULL
          AND j.skill_version IS NOT NULL
          AND s.skill_name = j.skill_name
          AND CAST(s.version_label AS TEXT) = j.skill_version
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM jobs
                WHERE job_type IN ('log_triage', 'daily_report')
                  AND skill_snapshot_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot enforce job snapshot provenance: legacy AI jobs remain unmapped; backfill jobs.skill_snapshot_id first';
            END IF;
        END $$
        """
    )
    # The explicit unmapped-row check above makes this safe to validate as
    # part of the same migration rather than leaving a partially enforced
    # provenance rule behind.
    op.execute(
        """
        ALTER TABLE jobs
        ALTER COLUMN skill_snapshot_id SET NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE jobs ALTER COLUMN skill_snapshot_id DROP NOT NULL")
