"""Skill Runtime mission Phase 11: regression tests for the outbox
dispatcher's claim semantics.

No prior test file exercised app/outbox.py directly at all -- the
concurrent-dispatcher race (a whole `SELECT ... FOR UPDATE SKIP LOCKED`
batch's row locks all releasing on the *first* row's commit, exposing
every other still-unpublished row in that batch to a second dispatcher)
was real but untested. These tests run two genuinely concurrent
dispatchers (separate DB sessions/threads, deliberately overlapped via a
barrier) against the same real Postgres and prove every row is published
exactly once between them -- never zero, never twice.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

from app.models.models import Job, OutboxEvent, SkillSnapshot
from app.outbox import dispatch_pending_events, purge_published_events

from tests.conftest import TestSessionLocal


def _make_pending_event(db, *, routing_key="log_triage") -> OutboxEvent:
    snapshot = db.query(SkillSnapshot).filter(SkillSnapshot.skill_name == "outbox-test").one_or_none()
    if snapshot is None:
        snapshot = SkillSnapshot(
            skill_name="outbox-test",
            version_label=1,
            content_hash="b" * 64,
            skill_md="# outbox test",
            output_schema_json='{"type":"object"}',
            manifest_yaml="name: outbox-test\n",
            is_active=True,
        )
        db.add(snapshot)
        db.flush()
    job = Job(
        job_type="log_triage",
        status="QUEUED",
        skill_name="outbox-test",
        skill_version="1",
        skill_hash=snapshot.content_hash,
        skill_snapshot_id=snapshot.id,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(job)
    db.flush()
    event = OutboxEvent(
        event_type="job.created",
        aggregate_type="job",
        aggregate_id=job.id,
        job_id=job.id,
        routing_key=routing_key,
        payload={"job_id": str(job.id)},
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


class _RecordingChannel:
    """Stands in for the real pika channel: publish_message ultimately
    just needs *a* channel object to pass through to a monkeypatched
    publish_message, so this can be a bare sentinel."""


def _make_slow_publish(published: list, lock: threading.Lock, barrier: threading.Barrier, delay_s: float = 0.05):
    """A fake publish_message that (a) records every (row-identifying)
    call under a lock, so two threads' publishes are never lost to a
    race in the test's own bookkeeping, and (b) waits at a barrier before
    its *first* call so both dispatcher threads are guaranteed to be
    mid-batch at the same time -- maximizing the odds of reproducing the
    old race if the fix regressed."""
    hit_barrier = threading.Event()

    def _fake_publish_message(channel, *, exchange, routing_key, payload, message_id):
        if not hit_barrier.is_set():
            hit_barrier.set()
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
        import time as _time

        _time.sleep(delay_s)
        with lock:
            published.append(payload["job_id"])

    return _fake_publish_message


def test_two_concurrent_dispatchers_never_publish_the_same_row_twice(monkeypatch):
    """The core Phase 11 regression: N pending rows, two dispatchers
    racing over them concurrently, must publish each row exactly once in
    total -- not the pre-fix behavior where the second dispatcher could
    re-claim a row the first had already locked (but not yet published)
    once the first dispatcher's earlier row committed."""
    setup_db = TestSessionLocal()
    try:
        job_ids = [str(_make_pending_event(setup_db).job_id) for _ in range(4)]
    finally:
        setup_db.close()

    published: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    fake_publish = _make_slow_publish(published, lock, barrier)
    monkeypatch.setattr("app.outbox.publish_message", fake_publish)

    results: dict[str, int] = {}

    def _run(name: str):
        db = TestSessionLocal()
        try:
            results[name] = dispatch_pending_events(db, _RecordingChannel(), batch_size=10)
        finally:
            db.close()

    t1 = threading.Thread(target=_run, args=("a",))
    t2 = threading.Thread(target=_run, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert sorted(published) == sorted(job_ids), (
        f"expected each of {job_ids} published exactly once, got {published}"
    )
    assert results["a"] + results["b"] == len(job_ids)

    verify_db = TestSessionLocal()
    try:
        rows = verify_db.query(OutboxEvent).all()
        assert all(row.published_at is not None for row in rows)
    finally:
        verify_db.close()


def test_dispatch_pending_events_marks_row_published_and_returns_count(monkeypatch):
    db = TestSessionLocal()
    try:
        event = _make_pending_event(db)
        event.last_error = "stale channel failure"
        db.commit()
        published_payloads = []
        monkeypatch.setattr(
            "app.outbox.publish_message",
            lambda channel, **kw: published_payloads.append(kw["payload"]),
        )

        count = dispatch_pending_events(db, _RecordingChannel())

        assert count == 1
        assert published_payloads == [{"job_id": str(event.job_id)}]
        db.refresh(event)
        assert event.published_at is not None
        assert event.last_error is None
    finally:
        db.close()


def test_dispatch_pending_events_does_not_starve_other_rows_on_repeated_failure(monkeypatch):
    """A row whose publish keeps failing (e.g. broker down) must not be
    re-selected within the same dispatch call, or it would starve every
    other pending row of a turn in that batch -- see the failed_ids
    tracking this asserts against."""
    db = TestSessionLocal()
    try:
        failing = _make_pending_event(db, routing_key="log_triage")
        ok = _make_pending_event(db, routing_key="log_triage")

        def _flaky_publish(channel, *, exchange, routing_key, payload, message_id):
            if payload["job_id"] == str(failing.job_id):
                raise RuntimeError("broker unreachable")

        monkeypatch.setattr("app.outbox.publish_message", _flaky_publish)

        count = dispatch_pending_events(db, _RecordingChannel(), batch_size=10)

        assert count == 1
        db.refresh(failing)
        db.refresh(ok)
        assert failing.published_at is None
        assert failing.attempt_count == 1
        assert ok.published_at is not None
    finally:
        db.close()


def test_purge_published_events_respects_cutoff_and_never_deletes_pending_rows():
    """Retention is strictly for old, confirmed publications.

    The cutoff itself is retained (the policy says *older than* the cutoff),
    as are recent publications and any event that still needs dispatch.
    """
    db = TestSessionLocal()
    try:
        now = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        old = _make_pending_event(db)
        boundary = _make_pending_event(db)
        recent = _make_pending_event(db)
        pending = _make_pending_event(db)
        old.published_at = now - timedelta(days=31)
        boundary.published_at = now - timedelta(days=30)
        recent.published_at = now - timedelta(days=1)
        old_id, boundary_id, recent_id, pending_id = (
            old.id,
            boundary.id,
            recent.id,
            pending.id,
        )
        db.commit()

        assert purge_published_events(db, retention_days=30, batch_size=50, now=now) == 1
        assert db.get(OutboxEvent, old_id) is None
        assert db.get(OutboxEvent, boundary_id) is not None
        assert db.get(OutboxEvent, recent_id) is not None
        assert db.get(OutboxEvent, pending_id) is not None
        assert db.get(OutboxEvent, pending_id).published_at is None
    finally:
        db.close()


def test_purge_published_events_is_bounded_and_idempotent():
    db = TestSessionLocal()
    try:
        now = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        events = [_make_pending_event(db) for _ in range(3)]
        for event in events:
            event.published_at = now - timedelta(days=31)
        db.commit()

        assert purge_published_events(db, retention_days=30, batch_size=2, now=now) == 2
        assert db.query(OutboxEvent).count() == 1
        assert purge_published_events(db, retention_days=30, batch_size=2, now=now) == 1
        assert db.query(OutboxEvent).count() == 0
        # A repeated cleanup pass is a safe no-op.
        assert purge_published_events(db, retention_days=30, batch_size=2, now=now) == 0
    finally:
        db.close()
