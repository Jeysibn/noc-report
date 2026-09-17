from datetime import datetime, timezone
from unittest.mock import patch

from app.evidence_purge_worker import sweep_pending_purges
from app.models.models import Evidence


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
