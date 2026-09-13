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
from datetime import datetime, timezone

import pytest

from app.models.models import Job, OutboxEvent
from app.outbox import dispatch_pending_events
from tests.conftest import TestSessionLocal


def _make_pending_event(db, *, routing_key="log_triage") -> OutboxEvent:
    job = Job(
        job_type="log_triage",
        status="QUEUED",
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
