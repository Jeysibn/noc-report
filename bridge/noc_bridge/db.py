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
    """Also releases the claim/lease (Reliability mission Batch A) — a
    COMPLETED job is a terminal state, so there's nothing left to protect
    a lease against, and clearing it keeps `fetch_job_row` output tidy."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, completed_at = %s, error_code = NULL, "
            "error_message = NULL, claimed_at = NULL, "
            "claim_token = NULL, lease_expires_at = NULL WHERE id = %s",
            ("COMPLETED", datetime.now(timezone.utc), str(job_id)),
        )
    conn.commit()


def mark_failed(conn, job_id: uuid.UUID, *, error_code: str, error_message: str) -> None:
    """Releases the claim/lease too — a FAILED job (whether headed for
    retry-via-DLX or the DLQ) must be reclaimable again, either by this
    worker on redelivery or by an admin's DLQ requeue."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, completed_at = %s, error_code = %s, "
            "error_message = %s, claimed_at = NULL, claim_token = NULL, "
            "lease_expires_at = NULL WHERE id = %s",
            ("FAILED", datetime.now(timezone.utc), error_code, error_message, str(job_id)),
        )
    conn.commit()


def bump_attempt(conn, job_id: uuid.UUID, attempt: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET attempt = %s WHERE id = %s", (attempt, str(job_id)))
    conn.commit()


def reserve_paid_ai_calls(conn, job_id: uuid.UUID, *, requested: int = 4) -> int:
    """Atomically reserve the remaining paid-call permits for one Job.

    Reservation is intentionally conservative: unused permits are not
    returned after a sandbox exits. That makes a worker crash or RabbitMQ
    redelivery unable to buy another Claude budget. Artifact reconciliation
    still bypasses this function entirely after Claude has already produced
    the deterministic artifact.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET paid_ai_calls_reserved = paid_ai_calls_reserved + LEAST(
                %(requested)s, paid_ai_call_budget - paid_ai_calls_reserved
            )
            WHERE id = %(job_id)s
              AND paid_ai_calls_reserved < paid_ai_call_budget
            RETURNING paid_ai_calls_reserved
            """,
            {"requested": requested, "job_id": str(job_id)},
        )
        row = cur.fetchone()
    conn.commit()
    return int(row[0]) if row else 0


def fetch_job_row(conn, job_id: uuid.UUID) -> dict | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (str(job_id),))
        return cur.fetchone()


# --- Idempotent job lifecycle (Reliability mission Batch A) -----------------
#
# RabbitMQ delivers at-least-once, so the same job message can be handed to
# this consumer more than once (broker-level redelivery after a dropped
# connection, an admin's manual DLQ requeue landing on top of an in-flight
# retry, etc.). Claiming a job here — rather than trusting "I received a
# message, therefore I should run it" — is what makes redelivery safe:
# claim_job only succeeds for a job that isn't already durably owned by a
# live lease, and the caller (service.py) checks Job.status == COMPLETED /
# artifact-already-uploaded before ever attempting a claim at all.

def claim_job(conn, job_id: uuid.UUID, *, worker_id: str, lease_seconds: int) -> dict | None:
    """Attempts to claim `job_id` for this worker. Succeeds (returns the
    updated row) only if the job is QUEUED/FAILED (a fresh or
    retry-eligible job), or PROCESSING with an *expired* lease (a prior
    claimant crashed without releasing it) — never for a job whose lease
    is still live, which means some other delivery (or a genuinely
    concurrent worker) is already handling it. Returns None on failure to
    claim; the caller must not execute Claude in that case."""
    claim_token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'PROCESSING',
                started_at = %(now)s,
                claimed_at = %(now)s,
                claim_token = %(claim_token)s,
                lease_expires_at = %(now)s + (%(lease_seconds)s * interval '1 second'),
                worker_id = %(worker_id)s
            WHERE id = %(job_id)s
              AND status != 'COMPLETED'
              AND (
                    status IN ('QUEUED', 'FAILED')
                    OR (status = 'PROCESSING' AND (lease_expires_at IS NULL OR lease_expires_at < %(now)s))
              )
            RETURNING *
            """,
            {
                "now": now,
                "claim_token": claim_token,
                "lease_seconds": lease_seconds,
                "worker_id": worker_id,
                "job_id": str(job_id),
            },
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def renew_job_lease(conn, job_id: uuid.UUID, claim_token: str, *, lease_seconds: int) -> bool:
    """Extend only the lease owned by this worker. A stale worker cannot
    renew a reclaimed job because the claim token changes on every claim."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET lease_expires_at = %(now)s + (%(seconds)s * interval '1 second') "
            "WHERE id = %(job_id)s AND status = 'PROCESSING' AND claim_token = %(token)s",
            {"now": now, "seconds": lease_seconds, "job_id": str(job_id), "token": claim_token},
        )
        renewed = cur.rowcount == 1
    conn.commit()
    return renewed


# Milestone 17 gap follow-up (AI Configuration, "real config, live-wired to
# the bridge" — operator's explicit scope choice): system_config is a
# single-row table (apps/api/app/seed.py's SYSTEM_CONFIG_ID), read fresh on
# every job dispatch so an admin's PATCH via the API takes effect on the
# very next job without a bridge restart.
_SYSTEM_CONFIG_ID = "00000000-0000-0000-0000-000000000001"

_SYSTEM_CONFIG_DEFAULTS = {
    "default_model": "claude-sonnet-5",
    # Cost-optimization mission Phase 4: low effort by default, escalated
    # per-job by the sandbox entrypoint when warranted (see
    # sandbox/entrypoint.py::_escalation_reason).
    "default_effort": "low",
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
