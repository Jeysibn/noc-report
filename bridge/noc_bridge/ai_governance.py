"""Single seam for paid-AI policy used by the bridge.

The database owns the durable counters; this module owns the policy that
decides which effort is effective and delegates atomic reservation and
consumption operations to the persistence layer. Keeping these decisions in
one place prevents callers from accidentally treating a broker retry as a
fresh AI budget.
"""
from __future__ import annotations

import uuid
import json
from pathlib import Path

from noc_bridge import db

MAX_PAID_AI_CALLS_PER_JOB = 4
_POLICY_PATH = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "ai_execution_policy.json"
_POLICY = json.loads(_POLICY_PATH.read_text())
SUPPORTED_MODELS = frozenset(_POLICY["supported_models"])
SUPPORTED_EFFORTS = frozenset(_POLICY["supported_efforts"])


class InvalidExecutionPolicy(RuntimeError, ValueError):
    """A durable job/config policy cannot be executed safely.

    This is intentionally a RuntimeError so the bridge's existing durable
    failure path marks the job failed and routes it to retry/DLQ rather than
    allowing a bad database value to escape the RabbitMQ callback.
    """


def effective_effort(requested: str | None, configured: str | None) -> str:
    """Return the explicit override or the configured system policy."""
    value = requested or configured or "low"
    value = str(value).lower()
    if value not in SUPPORTED_EFFORTS:
        raise InvalidExecutionPolicy(f"unsupported AI effort: {value!r}")
    return value


def effective_model(requested: str | None, configured: str | None) -> str:
    """Return an explicit model override or the live system default."""
    value = requested or configured or "claude-sonnet-5"
    value = str(value).strip()
    if value not in SUPPORTED_MODELS:
        raise InvalidExecutionPolicy(f"unsupported AI model: {value!r}")
    return value


def validate_system_config(config: dict) -> None:
    """Validate live admin configuration before it affects QoS or a job."""
    try:
        effective_model(None, config.get("default_model"))
        effective_effort(None, config.get("default_effort"))
        timeout = int(config["job_timeout_seconds"])
        concurrency = int(config["max_concurrent_jobs"])
        budget = float(config["claude_max_budget_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidExecutionPolicy(f"invalid stored AI execution policy: {exc}") from exc
    bounds = _POLICY
    if not bounds["job_timeout_seconds"]["min"] <= timeout <= bounds["job_timeout_seconds"]["max"]:
        raise InvalidExecutionPolicy("job timeout is outside the supported range")
    if not bounds["max_concurrent_jobs"]["min"] <= concurrency <= bounds["max_concurrent_jobs"]["max"]:
        raise InvalidExecutionPolicy("bridge capacity must be exactly one for the serial consumer")
    if not bounds["claude_max_budget_usd"]["min"] <= budget <= bounds["claude_max_budget_usd"]["max"]:
        raise InvalidExecutionPolicy("Claude budget is outside the supported range")


def reserve(conn, job_id: uuid.UUID, requested: int = MAX_PAID_AI_CALLS_PER_JOB) -> int:
    return db.reserve_paid_ai_calls(conn, job_id, requested=requested)


def record(conn, job_id: uuid.UUID, consumed: int) -> None:
    db.record_paid_ai_calls(conn, job_id, consumed)


def remaining(conn, job_id: uuid.UUID) -> int:
    return db.remaining_paid_ai_calls(conn, job_id)


def apply_effective_policy(conn, job_id: uuid.UUID, *, model: str, effort: str) -> None:
    """Persist the resolved execution policy for audit/UI visibility."""
    db.record_effective_ai_policy(conn, job_id, model=model, effort=effort)
