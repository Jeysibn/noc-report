"""migrate_default_effort_to_low

AI cost-optimization mission, Phase 2, Issue 3: `SystemConfig.default_effort`'s
Python-level default changed from "medium" to "low" back in Phase 1
(apps/api/app/models/models.py), but a *Python column default* only applies
to a freshly-inserted row — an already-seeded `system_config` row (this
table is a single row per deployment; see bridge/noc_bridge/db.py's
`_SYSTEM_CONFIG_DEFAULTS` seed) keeps whatever value was written when it was
first created, forever, regardless of later code changes.

Policy (documented, not silent): this migration updates the existing row's
`default_effort` from "medium" to "low" ONLY when it is still exactly
"medium" today. This is a heuristic, not a certainty — a deployment could
have an admin who deliberately chose "medium" as their own considered
setting (e.g. via the AI Configuration admin page) rather than one that
simply never got past the old hardcoded default. There is no column here
that distinguishes "never touched" from "deliberately set to medium"
(`updated_at` is stamped on the seeding insert too), so this migration
cannot tell the two apart. Given the explicit mission intent ("the desired
default after deployment is LOW"), this migration takes that as the
correct outcome for a deployment still sitting on the old default, and
accepts the (documented) risk of also moving an admin's deliberate
"medium" choice. Any other effort value (e.g. "high", or an already-"low"
row) is left untouched.

Downgrade cannot restore the original value (it was never recorded) — it
is a no-op with a clear comment, not a silent revert to "medium" for rows
that may have started this migration already at "low" on purpose.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

system_config = sa.table(
    "system_config",
    sa.column("default_effort", sa.String),
)


def upgrade() -> None:
    op.execute(
        system_config.update()
        .where(system_config.c.default_effort == "medium")
        .values(default_effort="low")
    )


def downgrade() -> None:
    # Deliberately a no-op: the pre-migration value was never recorded
    # anywhere, so there is nothing safe to restore. See module docstring.
    pass
