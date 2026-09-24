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
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.storage import delete_object_versions, evidence_object_key
from app.db.session import SessionLocal
from app.evidence_lifecycle import EvidencePurgePlan, purge_storage, references_for
from app.models.models import Evidence, EvidenceUploadIntent

logger = logging.getLogger("app.evidence_purge_worker")


def sweep_abandoned_upload_intents(db, *, limit: int = 50) -> int:
    """Expire stale upload intents and remove only their exact server-owned object.

    The row lock serializes this cleanup with `/complete`. A completion that
    already holds the row lock wins; once this sweep owns an expired intent,
    completion cannot create Evidence from the same upload while deletion is
    in progress. Storage errors leave the intent retryable.
    """
    settled = 0
    skipped_ids = set()
    now = datetime.now(timezone.utc)
    for _ in range(limit):
        query = select(EvidenceUploadIntent).where(
            EvidenceUploadIntent.state.in_(("OPEN", "EXPIRED")),
            EvidenceUploadIntent.expires_at <= now,
        )
        if skipped_ids:
            query = query.where(~EvidenceUploadIntent.id.in_(skipped_ids))
        intent = db.scalar(
            query.order_by(EvidenceUploadIntent.expires_at, EvidenceUploadIntent.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if intent is None:
            db.rollback()
            break

        identity_is_valid = (
            intent.bucket == settings.minio_bucket_evidence
            and intent.object_key
            == evidence_object_key(
                str(intent.incident_id),
                intent.evidence_type,
                intent.original_filename,
                intent.id,
            )
        )
        if intent.evidence_id is not None:
            logger.warning(
                "upload intent cleanup skipped intent_id=%s reason=%s",
                intent.id,
                "evidence_reference",
            )
            intent.state = "COMPLETED"
            try:
                db.commit()
                settled += 1
            except Exception:
                db.rollback()
                logger.exception("upload intent settlement failed intent_id=%s", intent.id)
            continue
        if not identity_is_valid:
            logger.error("upload intent cleanup refused mismatched storage identity intent_id=%s", intent.id)
            skipped_ids.add(intent.id)
            intent.state = "EXPIRED"
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("mismatched upload intent could not be expired intent_id=%s", intent.id)
            continue

        referenced = db.scalar(
            select(Evidence.id).where(
                Evidence.bucket == intent.bucket,
                Evidence.object_key == intent.object_key,
            ).limit(1)
        )
        if referenced is not None:
            logger.warning("upload intent cleanup skipped referenced object intent_id=%s", intent.id)
            intent.state = "COMPLETED"
            intent.evidence_id = referenced
            try:
                db.commit()
                settled += 1
            except Exception:
                db.rollback()
                logger.exception("upload intent settlement failed intent_id=%s", intent.id)
            continue

        try:
            deleted_versions = delete_object_versions(intent.bucket, intent.object_key)
            if deleted_versions:
                logger.info(
                    "orphan upload object removed intent_id=%s versions=%s",
                    intent.id,
                    deleted_versions,
                )
            intent.state = "CLEANED"
            db.commit()
            settled += 1
        except Exception:
            db.rollback()
            skipped_ids.add(intent.id)
            logger.exception("upload intent cleanup failed intent_id=%s; retrying later", intent.id)
    return settled


def sweep_pending_purges(db, *, limit: int = 50) -> int:
    """Attempt up to ``limit`` pending purges and return successful count.

    ``SKIP LOCKED`` allows more than one maintenance worker to run without
    making the same tombstone the normal work item at the same time. MinIO
    deletion is idempotent for an already-missing exact version.
    """
    completed = 0
    skipped_ids = set()
    for _ in range(limit):
        query = select(Evidence).where(Evidence.lifecycle_state == "PURGE_PENDING")
        if skipped_ids:
            query = query.where(~Evidence.id.in_(skipped_ids))
        record = db.scalar(query.order_by(Evidence.deleted_at, Evidence.created_at).with_for_update(skip_locked=True).limit(1))
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
            # Rollback restores the pending state, so exclude this record
            # for the remainder of this sweep. Otherwise one newly
            # protected oldest row can consume the whole bounded sweep and
            # starve later eligible evidence.
            skipped_ids.add(record.id)
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
            sweep_abandoned_upload_intents(db)
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
