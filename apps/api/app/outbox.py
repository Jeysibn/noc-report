"""Outbox dispatcher — Reliability mission Batch A.

Publishes `OutboxEvent` rows that were committed by app/jobs.py's
`enqueue_job()` (itself called from inside a single request-handler
transaction alongside the Job + AnalysisRun/Report/ReportSnapshot it
belongs to) onto RabbitMQ, exactly once each under normal operation and
at-least-once under failure — a message is only ever published *after*
the domain state it describes is durable in Postgres, never before.

Dispatcher behavior (per the reliability mission spec):

    find unpublished events
            |
            v
        publish
            |
            v
    publisher confirm
            |
            v
    mark published

A publish failure (broker unreachable, nacked/returned message) leaves
the row unpublished (`published_at IS NULL`) with `attempt_count`
incremented and `last_error` recorded, so the next dispatch pass retries
it.  Published history is cleaned separately by
`purge_published_events`, using a configurable age and bounded batches;
unpublished rows are never eligible for cleanup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.queue import JOBS_EXCHANGE, publish_message
from app.models.models import OutboxEvent

logger = logging.getLogger("app.outbox")

DEFAULT_BATCH_SIZE = 50


def purge_published_events(
    db: Session,
    *,
    retention_days: int,
    batch_size: int,
    now: datetime | None = None,
) -> int:
    """Delete one bounded batch of old, successfully published events.

    This maintenance operation deliberately has a narrow eligibility
    predicate: only rows with a non-null ``published_at`` older than the
    retention cutoff can be removed.  Pending rows (``published_at IS NULL``)
    remain durable until the dispatcher confirms publication.  Selecting IDs
    with row locks before deleting them lets multiple dispatcher instances
    run cleanup concurrently without deleting the same row twice.

    ``now`` is injectable for deterministic boundary tests.  As with the
    dispatcher, this helper owns the transaction for the rows it cleans and
    should be called with a maintenance/session-only SQLAlchemy session.
    """
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    cutoff = current_time - timedelta(days=retention_days)

    eligible_ids = db.scalars(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.published_at.is_not(None),
            OutboxEvent.published_at < cutoff,
        )
        .order_by(OutboxEvent.published_at.asc(), OutboxEvent.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    if not eligible_ids:
        # End the read transaction opened by SELECT without affecting any
        # caller work.  The worker supplies a dedicated maintenance session.
        db.rollback()
        return 0

    deleted = db.execute(
        delete(OutboxEvent).where(
            OutboxEvent.id.in_(eligible_ids),
            OutboxEvent.published_at.is_not(None),
            OutboxEvent.published_at < cutoff,
        )
    ).rowcount or 0
    db.commit()
    logger.info(
        "outbox retention cleanup deleted %s published events older than %s",
        deleted,
        cutoff.isoformat(),
    )
    return deleted


def dispatch_pending_events(db: Session, channel, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Publishes up to `batch_size` unpublished OutboxEvent rows, oldest
    first. Returns the number of rows successfully published in this
    pass.

    Skill Runtime mission Phase 11: this used to `SELECT ... FOR UPDATE
    SKIP LOCKED LIMIT batch_size` once, then loop over the whole batch
    committing after each row. That was a real race, not just a
    theoretical one: a Postgres row lock is held for the lifetime of the
    *transaction*, not the statement — so the very first `db.commit()`
    inside the loop released the locks on every *other* row still in that
    same SELECT's result set, not just the one just published. A second
    concurrent dispatcher's own `FOR UPDATE SKIP LOCKED` could then claim
    and publish one of those now-unlocked-but-not-yet-published rows,
    duplicating the RabbitMQ message before this loop got to it.

    Claiming one row per transaction (select-for-update, publish, mark
    published, commit, repeat) closes that window: a row's lock is held
    for exactly as long as it takes to publish and mark it, and is never
    silently dropped on an unrelated row's commit. `FOR UPDATE SKIP
    LOCKED` still lets more than one dispatcher process run concurrently
    (e.g. two API replicas) without both trying to publish the same row."""
    # A row that fails to publish this call stays published_at IS NULL
    # (retried on the *next* dispatch pass) but must not be re-selected
    # within this same call — with rows claimed one at a time, nothing
    # else would stop a persistently-failing row (e.g. broker down) from
    # being reselected every remaining iteration, starving every other
    # pending row of a turn in this batch.
    failed_ids: set = set()
    published = 0
    for _ in range(batch_size):
        query = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if failed_ids:
            query = query.where(OutboxEvent.id.not_in(failed_ids))
        row = db.scalar(query)
        if row is None:
            break

        try:
            publish_message(
                channel,
                exchange=JOBS_EXCHANGE,
                routing_key=row.routing_key,
                payload=row.payload,
                message_id=str(row.job_id),
            )
        except Exception as exc:  # noqa: BLE001 - broker/network errors of any shape
            logger.warning("outbox publish failed for event %s: %s", row.id, exc)
            db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == row.id)
                .values(attempt_count=OutboxEvent.attempt_count + 1, last_error=str(exc)[:2000])
            )
            db.commit()
            failed_ids.add(row.id)
            continue

        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == row.id)
            .values(published_at=func.now(), last_error=None)
        )
        db.commit()
        published += 1
    return published
