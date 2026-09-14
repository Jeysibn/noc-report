"""Outbox dispatcher loop — Reliability mission Batch A.

Runs `app.outbox.dispatch_pending_events` on a short interval, either as
its own standalone process (`python -m app.outbox_worker`, the intended
production shape — same precedent as `noc-claude-bridge.service` being
its own systemd unit rather than living inside the FastAPI process) or as
an in-process background thread started from `app.main`'s lifespan (dev
convenience, so `uvicorn app.main:app` alone is enough to see a job
actually reach RabbitMQ without a second process running).

Multiple instances can run concurrently: `dispatch_pending_events` uses
`SELECT ... FOR UPDATE SKIP LOCKED` and one-row transactions, so concurrent
dispatchers do not claim the same row during normal operation. RabbitMQ is
still at-least-once: a process crash after publish but before the commit can
cause a duplicate, which consumers must tolerate through their existing
idempotent job claim.
"""

from __future__ import annotations

import logging
import threading
import time

from app.core.queue import declare_topology, get_connection
from app.db.session import SessionLocal
from app.outbox import dispatch_pending_events

logger = logging.getLogger("app.outbox_worker")

POLL_INTERVAL_SECONDS = 1.0
RECONNECT_BACKOFF_SECONDS = 1.0
RECONNECT_BACKOFF_MAX_SECONDS = 30.0


def run_forever(stop_event: threading.Event | None = None) -> None:
    stop_event = stop_event or threading.Event()
    backoff = RECONNECT_BACKOFF_SECONDS
    while not stop_event.is_set():
        try:
            connection = get_connection()
            try:
                channel = connection.channel()
                declare_topology(channel)
                backoff = RECONNECT_BACKOFF_SECONDS
                while not stop_event.is_set():
                    db = SessionLocal()
                    try:
                        dispatch_pending_events(db, channel)
                    finally:
                        db.close()
                    stop_event.wait(POLL_INTERVAL_SECONDS)
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001 - broker/network errors of any shape
            logger.error("outbox dispatcher connection lost (%s) — reconnecting in %ss", exc, backoff)
            stop_event.wait(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SECONDS)


def start_background_thread() -> tuple[threading.Thread, threading.Event]:
    """Started from app.main's lifespan for local/dev convenience. In a
    real deployment, run `python -m app.outbox_worker` as its own process
    instead and skip this (see infrastructure/systemd)."""
    stop_event = threading.Event()
    thread = threading.Thread(target=run_forever, args=(stop_event,), name="outbox-dispatcher", daemon=True)
    thread.start()
    return thread, stop_event


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()


if __name__ == "__main__":
    main()
