"""Single seam for paid-AI policy used by the bridge.

The database owns the durable counters; this module owns the policy that
decides which effort is effective and delegates atomic reservation and
consumption operations to the persistence layer. Keeping these decisions in
one place prevents callers from accidentally treating a broker retry as a
fresh AI budget.
"""
from __future__ import annotations

import uuid

from noc_bridge import db

MAX_PAID_AI_CALLS_PER_JOB = 4
SUPPORTED_EFFORTS = frozenset({"low", "medium", "high"})


def effective_effort(requested: str | None, configured: str | None) -> str:
    """Return the explicit override or the configured system policy."""
    value = requested or configured or "low"
    value = str(value).lower()
    if value not in SUPPORTED_EFFORTS:
        raise ValueError(f"unsupported AI effort: {value!r}")
    return value


def reserve(conn, job_id: uuid.UUID, requested: int = MAX_PAID_AI_CALLS_PER_JOB) -> int:
    return db.reserve_paid_ai_calls(conn, job_id, requested=requested)


def record(conn, job_id: uuid.UUID, consumed: int) -> None:
    db.record_paid_ai_calls(conn, job_id, consumed)


def remaining(conn, job_id: uuid.UUID) -> int:
    return db.remaining_paid_ai_calls(conn, job_id)
