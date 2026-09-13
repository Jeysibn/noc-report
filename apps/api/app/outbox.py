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
it — outbox rows are never deleted here; retention/cleanup of old
published rows is a separate, deliberately unimplemented concern (no
retention policy has been decided yet).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.queue import JOBS_EXCHANGE, publish_message
from app.models.models import OutboxEvent

logger = logging.getLogger("app.outbox")

DEFAULT_BATCH_SIZE = 50


def dispatch_pending_events(db: Session, channel, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Publishes up to `batch_size` unpublished OutboxEvent rows, oldest
    first. `FOR UPDATE SKIP LOCKED` lets more than one dispatcher process
    run concurrently (e.g. two API replicas) without both trying to
    publish the same row. Returns the number of rows successfully
    published in this pass."""
    rows = list(
        db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    published = 0
    for row in rows:
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
            continue

        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == row.id)
            .values(published_at=func.now())
        )
        db.commit()
        published += 1
    return published
