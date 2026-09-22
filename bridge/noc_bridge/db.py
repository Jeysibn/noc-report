"""Direct Postgres job-status updates (master plan §26: "job status
persisted in PostgreSQL" / "RabbitMQ is not the source of truth"). Raw SQL
against the `jobs` table rather than importing apps/api's SQLAlchemy
models, for the same separate-deployable reason as `queue_topology.py` —
schema kept in sync by hand with `apps/api/app/models/models.py::Job`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import psycopg2
import psycopg2.extras


class JobClaimDisposition(str, Enum):
    """Outcome of an atomic Job lease claim.

    RabbitMQ delivery state is independent from the PostgreSQL lease.  A
    failed conditional UPDATE therefore cannot be represented by a boolean:
    a live lease means the delivery must be retried, while a completed Job is
    safe to ACK and a terminal row should be quarantined.
    """

    CLAIMED = "CLAIMED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    LEASE_BUSY = "LEASE_BUSY"
    NOT_CLAIMABLE = "NOT_CLAIMABLE"


@dataclass(frozen=True)
class JobClaimResult:
    """Structured result returned by :func:`claim_job`.

    ``job`` is populated only for a successful claim.  ``__getitem__`` and
    ``__bool__`` retain the old dict/truthiness ergonomics for callers that
    only need a successful claimed row while making the disposition explicit
    to broker-settlement code.
    """

    disposition: JobClaimDisposition
    job: dict | None = None

    @property
    def status(self) -> JobClaimDisposition:
        """Compatibility/readability alias for callers inspecting status."""

        return self.disposition

    def __bool__(self) -> bool:
        return self.disposition is JobClaimDisposition.CLAIMED

    def __getitem__(self, key):
        if self.job is None:
            raise KeyError(key)
        return self.job[key]


def get_connection(database_url: str):
    return psycopg2.connect(database_url)


def mark_started(conn, job_id: uuid.UUID, *, claim_token: str | None = None) -> bool:
    with conn.cursor() as cur:
        if claim_token is None:
            cur.execute(
                "UPDATE jobs SET status = %s, started_at = %s WHERE id = %s",
                ("PROCESSING", datetime.now(timezone.utc), str(job_id)),
            )
        else:
            cur.execute(
                "UPDATE jobs SET status = %s, started_at = %s WHERE id = %s AND claim_token = %s",
                ("PROCESSING", datetime.now(timezone.utc), str(job_id), claim_token),
            )
        updated = cur.rowcount == 1
    conn.commit()
    return updated


def mark_completed(
    conn, job_id: uuid.UUID, *, artifact_metadata: dict | None = None,
    claim_token: str | None = None,
) -> bool:
    """Also releases the claim/lease (Reliability mission Batch A) — a
    COMPLETED job is a terminal state, so there's nothing left to protect
    a lease against, and clearing it keeps `fetch_job_row` output tidy."""
    with conn.cursor() as cur:
        where = "WHERE id = %s" if claim_token is None else "WHERE id = %s AND claim_token = %s"
        where_params = (str(job_id),) if claim_token is None else (str(job_id), claim_token)
        if artifact_metadata is None:
            cur.execute(
                "UPDATE jobs SET status = %s, completed_at = %s, error_code = NULL, "
                "error_message = NULL, claimed_at = NULL, "
                "claim_token = NULL, lease_expires_at = NULL " + where,
                ("COMPLETED", datetime.now(timezone.utc), *where_params),
            )
        else:
            cur.execute(
                "UPDATE jobs SET status = %s, completed_at = %s, error_code = NULL, "
                "error_message = NULL, claimed_at = NULL, "
                "claim_token = NULL, lease_expires_at = NULL " + where,
                (
                    "COMPLETED",
                    datetime.now(timezone.utc),
                    *where_params,
                ),
            )
        updated = cur.rowcount == 1
        if not updated:
            conn.commit()
            return False
        if artifact_metadata is not None:
            report = artifact_metadata.get("report")
            if report:
                cur.execute(
                    "UPDATE reports SET report_bucket = %s, report_object_key = %s, report_version_id = %s, "
                    "report_sha256 = %s, report_byte_size = %s, report_content_type = %s WHERE job_id = %s",
                    (
                        report.get("bucket"), report.get("object_key"), report.get("version_id"),
                        report.get("sha256"), report.get("byte_size"), report.get("content_type"),
                        str(job_id),
                    ),
                )
            document = artifact_metadata.get("document")
            screenshots = artifact_metadata.get("screenshots")
            if document and screenshots:
                cur.execute(
                    "UPDATE reports SET document_object_key = %s, document_version_id = %s, document_sha256 = %s, "
                    "document_byte_size = %s, document_content_type = %s, "
                    "screenshots_object_key = %s, screenshots_version_id = %s, screenshots_sha256 = %s, "
                    "screenshots_byte_size = %s, screenshots_content_type = %s WHERE job_id = %s",
                    (
                        document.get("object_key"), document.get("version_id"), document.get("sha256"),
                        document.get("byte_size"), document.get("content_type"), screenshots.get("object_key"),
                        screenshots.get("version_id"), screenshots.get("sha256"), screenshots.get("byte_size"),
                        screenshots.get("content_type"), str(job_id),
                    ),
                )
    conn.commit()
    return True


def mark_failed(
    conn, job_id: uuid.UUID, *, error_code: str, error_message: str,
    claim_token: str | None = None,
) -> bool:
    """Releases the claim/lease too — a FAILED job (whether headed for
    retry-via-DLX or the DLQ) must be reclaimable again, either by this
    worker on redelivery or by an admin's DLQ requeue."""
    with conn.cursor() as cur:
        where = "WHERE id = %s" if claim_token is None else "WHERE id = %s AND claim_token = %s"
        params = ("FAILED", datetime.now(timezone.utc), error_code, error_message, str(job_id))
        if claim_token is not None:
            params = (*params, claim_token)
        cur.execute(
            "UPDATE jobs SET status = %s, completed_at = %s, error_code = %s, "
            "error_message = %s, claimed_at = NULL, claim_token = NULL, "
            "lease_expires_at = NULL " + where,
            params,
        )
        updated = cur.rowcount == 1
    conn.commit()
    return updated


def bump_attempt(conn, job_id: uuid.UUID, attempt: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET attempt = %s WHERE id = %s", (attempt, str(job_id)))
    conn.commit()


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
# live lease. The caller performs a fast completed/artifact reconciliation
# check first, then uses the explicit claim disposition to settle duplicate
# deliveries without confusing temporary contention for terminal work.

def claim_job(conn, job_id: uuid.UUID, *, worker_id: str, lease_seconds: int) -> JobClaimResult:
    """Attempt to claim ``job_id`` and classify every non-claim outcome.

    The conditional update remains the single ownership gate.  When it does
    not return a row, a locked read classifies the authoritative Job state so
    the RabbitMQ consumer can distinguish a temporary live-lease collision
    (NACK to the retry queue) from a completed or terminal Job (settle it
    permanently).  An expired ``PROCESSING`` lease is claimed by the same
    atomic update, preserving crash recovery.
    """
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
        if row:
            conn.commit()
            return JobClaimResult(JobClaimDisposition.CLAIMED, dict(row))

        # The failed UPDATE may mean a live lease, a concurrent completion, or
        # a status that is not executable.  Lock the row before classifying so
        # a concurrent claimant cannot change the answer between this read
        # and broker settlement.
        cur.execute("SELECT * FROM jobs WHERE id = %s FOR UPDATE", (str(job_id),))
        current = cur.fetchone()

    conn.commit()
    if current is None:
        return JobClaimResult(JobClaimDisposition.NOT_CLAIMABLE)

    current = dict(current)
    if current.get("status") == "COMPLETED":
        return JobClaimResult(JobClaimDisposition.ALREADY_COMPLETED, current)

    lease_expires_at = current.get("lease_expires_at")
    if current.get("status") == "PROCESSING" and lease_expires_at is not None:
        # PostgreSQL returns timezone-aware values for this timestamptz column.
        # Be defensive for lightweight test doubles and legacy rows.
        if lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
        if lease_expires_at >= now:
            return JobClaimResult(JobClaimDisposition.LEASE_BUSY, current)

    return JobClaimResult(JobClaimDisposition.NOT_CLAIMABLE, current)


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
