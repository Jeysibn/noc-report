from datetime import datetime, timezone
from unittest.mock import patch

from app.evidence_purge_worker import sweep_pending_purges
from app.models.models import Evidence, OcrRun


def _pending(db_session) -> Evidence:
    evidence = Evidence(
        evidence_type="LOG",
        bucket="noc-evidence",
        object_key="pending.log",
        original_filename="pending.log",
        lifecycle_state="PURGE_PENDING",
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add(evidence)
    db_session.commit()
    return evidence


def test_pending_purge_is_retried_and_marked_purged(db_session):
    evidence = _pending(db_session)
    with patch("app.evidence_purge_worker.purge_storage") as purge:
        assert sweep_pending_purges(db_session) == 1
        purge.assert_called_once()
    db_session.refresh(evidence)
    assert evidence.lifecycle_state == "PURGED"


def test_failed_pending_purge_remains_retryable(db_session):
    evidence = _pending(db_session)
    with patch("app.evidence_purge_worker.purge_storage", side_effect=RuntimeError("MinIO offline")):
        assert sweep_pending_purges(db_session) == 0
    db_session.refresh(evidence)
    assert evidence.lifecycle_state == "PURGE_PENDING"


def test_protected_oldest_pending_record_does_not_starve_later_records(db_session):
    oldest = _pending(db_session)
    oldest.deleted_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    later_a = _pending(db_session)
    later_a.deleted_at = datetime(2020, 1, 2, tzinfo=timezone.utc)
    later_b = _pending(db_session)
    later_b.deleted_at = datetime(2020, 1, 3, tzinfo=timezone.utc)
    db_session.commit()

    # An OCR reference makes the oldest record protected when the sweep
    # checks it. The rollback must not make it the same candidate forever.
    db_session.add(OcrRun(evidence_id=oldest.id, status="COMPLETED"))
    db_session.commit()
    with patch("app.evidence_purge_worker.purge_storage") as purge:
        assert sweep_pending_purges(db_session, limit=3) == 2
        assert purge.call_count == 2

    db_session.refresh(oldest)
    db_session.refresh(later_a)
    db_session.refresh(later_b)
    assert oldest.lifecycle_state == "PURGE_PENDING"
    assert later_a.lifecycle_state == "PURGED"
    assert later_b.lifecycle_state == "PURGED"
