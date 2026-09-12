"""Direct Postgres job-status updates (master plan §26: "job status
persisted in PostgreSQL" / "RabbitMQ is not the source of truth"). Raw SQL
against the `jobs` table rather than importing apps/api's SQLAlchemy
models, for the same separate-deployable reason as `queue_topology.py` —
schema kept in sync by hand with `apps/api/app/models/models.py::Job`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


def get_connection(database_url: str):
    return psycopg2.connect(database_url)


def mark_started(conn, job_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, started_at = %s WHERE id = %s",
            ("PROCESSING", datetime.now(timezone.utc), str(job_id)),
        )
    conn.commit()


def mark_completed(conn, job_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, completed_at = %s WHERE id = %s",
            ("COMPLETED", datetime.now(timezone.utc), str(job_id)),
        )
    conn.commit()


def mark_failed(conn, job_id: uuid.UUID, *, error_code: str, error_message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, completed_at = %s, error_code = %s, "
            "error_message = %s WHERE id = %s",
            ("FAILED", datetime.now(timezone.utc), error_code, error_message, str(job_id)),
        )
    conn.commit()


def bump_attempt(conn, job_id: uuid.UUID, attempt: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET attempt = %s WHERE id = %s", (attempt, str(job_id)))
    conn.commit()


def fetch_job_row(conn, job_id: uuid.UUID) -> dict | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (str(job_id),))
        return cur.fetchone()


# Milestone 17 gap follow-up (AI Configuration, "real config, live-wired to
# the bridge" — operator's explicit scope choice): system_config is a
# single-row table (apps/api/app/seed.py's SYSTEM_CONFIG_ID), read fresh on
# every job dispatch so an admin's PATCH via the API takes effect on the
# very next job without a bridge restart.
_SYSTEM_CONFIG_ID = "00000000-0000-0000-0000-000000000001"

_SYSTEM_CONFIG_DEFAULTS = {
    "default_model": "claude-sonnet-5",
    "default_effort": "medium",
    "job_timeout_seconds": 300,
    "max_concurrent_jobs": 1,
}


def load_system_config(conn) -> dict:
    """Returns the same defaults apps/api/app/models/models.py::SystemConfig
    declares if the row is somehow missing (a fresh DB that skipped
    seeding) — never raises, so a missing config row degrades to defaults
    rather than failing job dispatch."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT default_model, default_effort, job_timeout_seconds, "
            "max_concurrent_jobs FROM system_config WHERE id = %s",
            (_SYSTEM_CONFIG_ID,),
        )
        row = cur.fetchone()
    return dict(row) if row else dict(_SYSTEM_CONFIG_DEFAULTS)
