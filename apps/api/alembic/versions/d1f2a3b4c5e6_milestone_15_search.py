"""milestone_15_search

Master plan §32 (Search/Knowledge Architecture): PostgreSQL full-text +
trigram search over incidents. Enables pg_trgm (for fuzzy substring
matching on title/service/notes) and adds a GIN index over a
to_tsvector expression covering title/service/environment/notes, so
`GET /search` doesn't do a sequential scan.

Revision ID: d1f2a3b4c5e6
Revises: bc0238f0b2e8
Create Date: 2026-09-09 09:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1f2a3b4c5e6'
down_revision: Union[str, Sequence[str], None] = 'bc0238f0b2e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX incidents_fts_idx ON incidents
        USING GIN (
            to_tsvector(
                'english',
                coalesce(title, '') || ' ' || coalesce(service, '') || ' '
                || coalesce(environment, '') || ' ' || coalesce(notes, '')
            )
        )
        """
    )
    op.execute("CREATE INDEX incidents_title_trgm_idx ON incidents USING GIN (title gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS incidents_title_trgm_idx")
    op.execute("DROP INDEX IF EXISTS incidents_fts_idx")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
