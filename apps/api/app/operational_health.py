"""Real dependency/readiness checks for the operator console.

The liveness endpoint remains cheap and process-local. These checks are for
readiness and diagnostics, so an unavailable dependency is reported as such
instead of being converted into a false green dashboard state.
"""
from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pika
from sqlalchemy import text

from app.core.config import settings
from app.core.queue import JOB_TYPES, queue_names
from app.core.storage import get_client
from app.db.session import SessionLocal


def _check_database() -> dict:
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
        return {"status": "healthy"}
    except Exception as exc:
        return {"status": "unavailable", "detail": type(exc).__name__}


def _check_rabbitmq() -> dict:
    try:
        params = pika.URLParameters(settings.rabbitmq_url)
        params.connection_attempts = 1
        params.socket_timeout = settings.health_timeout_seconds
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        queues = {}
        for job_type in JOB_TYPES:
            names = queue_names(job_type)
            queues[job_type] = {
                "ready": channel.queue_declare(names["main"], passive=True).method.message_count,
                "dead_letter": channel.queue_declare(names["dlq"], passive=True).method.message_count,
            }
        connection.close()
        return {"status": "healthy", "detail": {"queues": queues}}
    except Exception as exc:
        return {"status": "unavailable", "detail": type(exc).__name__}


def _check_minio() -> dict:
    try:
        get_client().list_buckets()
        return {"status": "healthy"}
    except Exception as exc:
        return {"status": "unavailable", "detail": type(exc).__name__}


def _check_http_dependency(url: str, *, name: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=settings.health_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = payload.get("status")
        if status == "healthy":
            return {"status": "healthy", "detail": payload}
        if status in {"degraded", "unavailable"}:
            return {"status": status, "detail": payload}
        return {"status": "unknown", "detail": payload}
    except Exception as exc:
        return {"status": "unavailable", "detail": f"{name}: {type(exc).__name__}"}


def _overall_status(dependencies: dict[str, dict]) -> str:
    statuses = {item.get("status") for item in dependencies.values()}
    if "unavailable" in statuses:
        return "unavailable"
    if "degraded" in statuses:
        return "degraded"
    if "unknown" in statuses:
        return "unknown"
    return "healthy"


def dependency_health() -> dict:
    checks = {
        "postgresql": _check_database,
        "rabbitmq": _check_rabbitmq,
        "minio": _check_minio,
        "claude_bridge": lambda: _check_http_dependency(settings.bridge_health_url, name="bridge"),
    }
    if settings.local_prefill_ai_enabled:
        checks["ollama"] = lambda: _check_http_dependency(
            f"{settings.local_prefill_base_url.rstrip('/')}/api/tags", name="ollama"
        )
    else:
        checks["ollama"] = lambda: {"status": "unknown", "detail": "disabled"}

    dependencies: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = {executor.submit(check): name for name, check in checks.items()}
        for future in as_completed(futures):
            dependencies[futures[future]] = future.result()
    return {
        "status": _overall_status(dependencies),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dependencies": dependencies,
    }
