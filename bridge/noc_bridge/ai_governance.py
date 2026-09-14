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


def effective_model(requested: str | None, configured: str | None) -> str:
    """Return an explicit model override or the live system default."""
    value = requested or configured or "claude-sonnet-5"
    value = str(value).strip()
    if not value:
        raise ValueError("effective AI model cannot be empty")
    return value


def reserve(conn, job_id: uuid.UUID, requested: int = MAX_PAID_AI_CALLS_PER_JOB) -> int:
    return db.reserve_paid_ai_calls(conn, job_id, requested=requested)


def record(conn, job_id: uuid.UUID, consumed: int) -> None:
    db.record_paid_ai_calls(conn, job_id, consumed)


def remaining(conn, job_id: uuid.UUID) -> int:
    return db.remaining_paid_ai_calls(conn, job_id)


def apply_effective_policy(conn, job_id: uuid.UUID, *, model: str, effort: str) -> None:
    """Persist the resolved execution policy for audit/UI visibility."""
    db.record_effective_ai_policy(conn, job_id, model=model, effort=effort)
