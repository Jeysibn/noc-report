"""Retry worker for committed Evidence ``PURGE_PENDING`` tombstones.

The database transition is the durable part of a purge request. Storage
cleanup is deliberately retried here, using the exact object version stored
on the tombstone. A failed attempt rolls back only the worker transaction;
the tombstone remains non-visible and is safe to retry after a restart.
"""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.evidence_lifecycle import EvidencePurgePlan, purge_storage, references_for
from app.models.models import Evidence

logger = logging.getLogger("app.evidence_purge_worker")


def sweep_pending_purges(db, *, limit: int = 50) -> int:
    """Attempt up to ``limit`` pending purges and return successful count.

    ``SKIP LOCKED`` allows more than one maintenance worker to run without
    making the same tombstone the normal work item at the same time. MinIO
    deletion is idempotent for an already-missing exact version.
    """
    completed = 0
    for _ in range(limit):
        record = db.scalar(
            select(Evidence)
            .where(Evidence.lifecycle_state == "PURGE_PENDING")
            .order_by(Evidence.deleted_at, Evidence.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if record is None:
            db.rollback()
            break

        references = references_for(db, record.id)
        if references:
            logger.warning(
                "evidence %s became protected while purge was pending: %s",
                record.id,
                ", ".join(references),
            )
            db.rollback()
            continue

        plan = EvidencePurgePlan(record.id, record.bucket, record.object_key, record.version_id)
        try:
            purge_storage(plan)
            record.lifecycle_state = "PURGED"
            db.commit()
            completed += 1
        except Exception:
            db.rollback()
            logger.exception("evidence purge failed; leaving %s pending", record.id)
    return completed


def run_forever(stop_event: threading.Event | None = None) -> None:
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            sweep_pending_purges(db)
        except Exception:
            db.rollback()
            logger.exception("pending evidence purge sweep failed")
        finally:
            db.close()
        stop_event.wait(settings.evidence_purge_interval_seconds)


def start_background_thread() -> tuple[threading.Thread, threading.Event]:
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_forever,
        args=(stop_event,),
        name="evidence-purge-worker",
        daemon=True,
    )
    thread.start()
    return thread, stop_event


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()


if __name__ == "__main__":
    main()
