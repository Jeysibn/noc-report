"""Focused lease/RabbitMQ settlement regressions.

These tests use the bridge's database and delivery seams directly.  The
RabbitMQ topology tests already prove that ``requeue=False`` from a main
queue routes to the delayed retry queue; these tests prove the consumer picks
that settlement for live lease contention and never runs the paid-AI path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from noc_bridge import db
from noc_bridge.service import BridgeService


class _Cursor:
    def __init__(self, rows):
        self._rows = iter(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        try:
            return next(self._rows)
        except StopIteration:
            return None


class _Connection:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    def cursor(self, **_kwargs):
        return _Cursor(self._rows)

    def commit(self):
        self.commits += 1


def test_claim_classifies_live_lease_without_consuming_it():
    now = datetime.now(timezone.utc)
    current = {
        "status": "PROCESSING",
        "lease_expires_at": now + timedelta(minutes=5),
    }

    result = db.claim_job(
        _Connection([None, current]),
        uuid.uuid4(),
        worker_id="worker-b",
        lease_seconds=60,
    )

    assert result.disposition is db.JobClaimDisposition.LEASE_BUSY
    assert result.job == current


def test_claim_reclaims_expired_processing_lease():
    now = datetime.now(timezone.utc)
    claimed_row = {
        "status": "PROCESSING",
        "claim_token": "new-token",
        "lease_expires_at": now + timedelta(minutes=5),
    }

    result = db.claim_job(
        _Connection([claimed_row]),
        uuid.uuid4(),
        worker_id="worker-b",
        lease_seconds=60,
    )

    assert result.disposition is db.JobClaimDisposition.CLAIMED
    assert result["claim_token"] == "new-token"


def test_claim_classifies_completed_duplicate():
    current = {"status": "COMPLETED", "lease_expires_at": None}
    result = db.claim_job(
        _Connection([None, current]),
        uuid.uuid4(),
        worker_id="worker-b",
        lease_seconds=60,
    )
    assert result.disposition is db.JobClaimDisposition.ALREADY_COMPLETED
    assert not result


def test_claim_reclaims_processing_row_with_no_lease():
    """Legacy/incomplete PROCESSING rows remain recoverable."""
    claimed_row = {"status": "PROCESSING", "claim_token": "new-token"}

    result = db.claim_job(
        _Connection([claimed_row]),
        uuid.uuid4(),
        worker_id="worker-b",
        lease_seconds=60,
    )

    assert result.disposition is db.JobClaimDisposition.CLAIMED


def _payload(job_id: uuid.UUID) -> dict:
    return {
        "protocol_version": 1,
        "job_id": str(job_id),
        "job_type": "log_triage",
        "incident_id": None,
        "object_refs": [],
        "model": "claude-sonnet-5",
        "effort": "medium",
        "skill_name": "log-triage-summary",
        "skill_version": "1",
        "skill_hash": None,
        "skill_execution_hash": None,
        "skill_snapshot_id": None,
        "expected_output_type": "log_triage.result",
        "correlation_id": str(uuid.uuid4()),
        "attempt": 1,
    }


def _method():
    return SimpleNamespace(delivery_tag=17)


def test_live_lease_redelivery_is_delayed_not_acked(monkeypatch):
    """A redelivery after a worker crash remains available until lease expiry."""
    job_id = uuid.uuid4()
    existing = {
        "status": "PROCESSING",
        "skill_snapshot_id": None,
        "skill_hash": None,
    }
    channel = Mock()
    monkeypatch.setattr("noc_bridge.service.db.fetch_job_row", lambda *_args: existing)
    monkeypatch.setattr(
        "noc_bridge.service.db.load_system_config",
        lambda *_args: {"job_timeout_seconds": 900, "max_concurrent_jobs": 1},
    )
    monkeypatch.setattr(
        "noc_bridge.service.db.claim_job",
        lambda *_args, **_kwargs: db.JobClaimResult(
            db.JobClaimDisposition.LEASE_BUSY, existing
        ),
    )
    bump_attempt = Mock()
    monkeypatch.setattr("noc_bridge.service.db.bump_attempt", bump_attempt)
    process = Mock(side_effect=AssertionError("live lease must not execute Claude"))
    service = BridgeService()
    monkeypatch.setattr(service, "_process_job", process)

    service._handle_delivery(
        channel,
        _method(),
        properties=None,
        body=__import__("json").dumps(_payload(job_id)).encode(),
        pg_conn=Mock(),
        minio_client=None,
        job_type="log_triage",
    )

    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)
    channel.basic_ack.assert_not_called()
    bump_attempt.assert_not_called()
    process.assert_not_called()


def test_completed_duplicate_is_acked_without_second_ai_execution(monkeypatch):
    """A delayed duplicate that returns after completion is safely ACKed."""
    job_id = uuid.uuid4()
    existing = {
        "status": "COMPLETED",
        "skill_snapshot_id": None,
        "skill_hash": None,
    }
    channel = Mock()
    monkeypatch.setattr("noc_bridge.service.db.fetch_job_row", lambda *_args: existing)
    process = Mock(side_effect=AssertionError("completed duplicate must not execute Claude"))
    service = BridgeService()
    monkeypatch.setattr(service, "_process_job", process)

    service._handle_delivery(
        channel,
        _method(),
        properties=None,
        body=__import__("json").dumps(_payload(job_id)).encode(),
        pg_conn=Mock(),
        minio_client=None,
        job_type="log_triage",
    )

    channel.basic_ack.assert_called_once_with(delivery_tag=17)
    channel.basic_nack.assert_not_called()
    process.assert_not_called()


def test_healthy_duplicate_is_delayed_then_acked_after_original_completes(monkeypatch):
    """A duplicate delivery does not race the live worker or execute Claude.

    The first delivery observes the original worker's live lease and is sent
    through the broker retry/DLX path.  Once that worker completes, the same
    delayed delivery observes COMPLETED and is settled with a normal ACK.
    """
    job_id = uuid.uuid4()
    live = {"status": "PROCESSING", "skill_snapshot_id": None, "skill_hash": None}
    completed = {"status": "COMPLETED", "skill_snapshot_id": None, "skill_hash": None}
    channel = Mock()
    fetch = Mock(side_effect=[live, completed])
    monkeypatch.setattr("noc_bridge.service.db.fetch_job_row", fetch)
    monkeypatch.setattr(
        "noc_bridge.service.db.load_system_config",
        lambda *_args: {"job_timeout_seconds": 900, "max_concurrent_jobs": 1},
    )
    claim = Mock(
        return_value=db.JobClaimResult(db.JobClaimDisposition.LEASE_BUSY, live),
    )
    monkeypatch.setattr("noc_bridge.service.db.claim_job", claim)
    process = Mock(side_effect=AssertionError("duplicate must never execute Claude"))
    service = BridgeService()
    monkeypatch.setattr(service, "_process_job", process)

    body = __import__("json").dumps(_payload(job_id)).encode()
    service._handle_delivery(
        channel, _method(), properties=None, body=body, pg_conn=Mock(), minio_client=None, job_type="log_triage"
    )
    channel.basic_nack.assert_called_once_with(delivery_tag=17, requeue=False)

    # The delayed retry is delivered later, after the original worker has
    # committed COMPLETED.  It must take the existing early-ACK path.
    service._handle_delivery(
        channel, _method(), properties=None, body=body, pg_conn=Mock(), minio_client=None, job_type="log_triage"
    )
    assert channel.basic_ack.call_count == 1
    assert claim.call_count == 1
    process.assert_not_called()
