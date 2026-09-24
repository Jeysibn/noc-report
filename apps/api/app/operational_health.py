"""Real dependency/readiness checks for the operator console.

The liveness endpoint remains cheap and process-local. These checks are for
readiness and diagnostics, so an unavailable dependency is reported as such
instead of being converted into a false green dashboard state.
"""
from __future__ import annotations

import json
import urllib.error
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
        if status in {"healthy", "ok"}:
            return {"status": "healthy", "detail": payload}
        if status in {"degraded", "unavailable"}:
            return {"status": status, "detail": payload}
        return {"status": "unknown", "detail": payload}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            status = payload.get("status")
            if status in {"degraded", "unavailable"}:
                return {"status": status, "detail": payload}
        except Exception:
            pass
        return {"status": "unavailable", "detail": f"{name}: HTTP {exc.code}"}
    except Exception as exc:
        return {"status": "unavailable", "detail": f"{name}: {type(exc).__name__}"}


def _check_ollama() -> dict:
    """Ollama's `/api/tags` is a model-list contract, not a generic health
    payload. Treat a reachable endpoint with the configured model present as
    healthy, and distinguish a reachable-but-unloaded model registry from a
    network failure."""
    url = f"{settings.local_prefill_base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=settings.health_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models") or []
        configured = settings.local_prefill_model
        names = {str(model.get("name")) for model in models if isinstance(model, dict)}
        if configured in names:
            return {"status": "healthy", "detail": {"model": configured, "model_count": len(models)}}
        return {"status": "degraded", "detail": {"model": configured, "model_count": len(models), "reason": "configured model is not installed"}}
    except Exception as exc:
        return {"status": "unavailable", "detail": f"ollama: {type(exc).__name__}"}


def _check_hermes() -> dict:
    """Hermes liveness is intentionally separate from API readiness.

    `/health` is unauthenticated and returns `{"status":"ok"}`. The worker
    remains responsible for authenticated capability/provider checks.
    """
    # Secondary Hermes profiles are served below /p/<profile>/v1 on the
    # default listener. Liveness is intentionally checked at the listener
    # root, not through the profile's OpenAI path.
    listener_url = settings.hermes_base_url.rstrip("/").split("/p/", 1)[0]
    return _check_http_dependency(f"{listener_url}/health", name="hermes")


def _check_ai_worker() -> dict:
    """Check the application-side worker separately from Hermes liveness.

    Hermes can be healthy while no consumer is claiming RabbitMQ jobs. This
    check is diagnostic only; core API readiness still depends on PostgreSQL,
    RabbitMQ, and MinIO rather than the optional AI pipeline.
    """
    return _check_http_dependency(settings.ai_worker_health_url, name="ai-worker")


def _worker_reports_hermes_healthy(worker: dict) -> bool:
    """Return whether the worker's authenticated runtime probe is healthy.

    In the supported Compose topology Hermes is intentionally reachable only
    from the AI Worker network. A host-run API therefore cannot use a direct
    Hermes HTTP probe as the source of truth; the worker's probe includes both
    Hermes reachability and the restricted-profile readiness check.
    """
    detail = worker.get("detail")
    runtime_detail = detail.get("metrics", detail) if isinstance(detail, dict) else {}
    dependencies = (
        detail.get("dependencies", runtime_detail.get("dependencies", {}))
        if isinstance(detail, dict)
        else {}
    )
    if isinstance(dependencies, dict) and dependencies:
        return (
            dependencies.get("hermes") == "healthy"
            and dependencies.get("hermes_profile") == "healthy"
        )

    # Backward compatibility while older workers are being rolled during an
    # upgrade. New workers provide explicit dependency statuses above.
    return (
        worker.get("status") == "healthy"
        and isinstance(detail, dict)
        and runtime_detail.get("runtime_ready") is True
        and runtime_detail.get("runtime_reachable") is True
    )


def _overall_status(dependencies: dict[str, dict]) -> str:
    # Disabled/not-applicable optional services do not make an otherwise
    # healthy installation unknown. Unknown still remains visible when a
    # configured dependency could not be classified.
    statuses = {item.get("status") for item in dependencies.values()} - {"disabled", "not_applicable"}
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
        "ai_runtime": _check_hermes if settings.ai_runtime == "hermes" else lambda: {
            "status": "disabled", "detail": "AI runtime is disabled by configuration"
        },
    }
    if settings.local_prefill_ai_enabled:
        checks["ollama"] = _check_ollama
    else:
        checks["ollama"] = lambda: {"status": "disabled", "detail": "local prefill AI is disabled by configuration"}
    if settings.ai_runtime == "hermes":
        checks["ai_worker"] = _check_ai_worker

    dependencies: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = {executor.submit(check): name for name, check in checks.items()}
        for future in as_completed(futures):
            dependencies[futures[future]] = future.result()

    # Hermes is intentionally internal-only in Compose. When FastAPI runs on
    # the host, its localhost/default Hermes probe is not expected to work;
    # the worker is the authenticated component that can actually reach and
    # validate the configured NOC profile. Avoid reporting a false AI outage
    # when that worker-side probe is healthy, while retaining the direct probe
    # result for diagnostics.
    if settings.ai_runtime == "hermes":
        direct_runtime = dependencies.get("ai_runtime")
        worker = dependencies.get("ai_worker")
        if isinstance(direct_runtime, dict) and _worker_reports_hermes_healthy(worker or {}):
            dependencies["ai_runtime"] = {
                "status": "healthy",
                "detail": {
                    "source": "ai-worker",
                    "runtime": worker["detail"].get("runtime"),
                    "profile": worker["detail"].get("profile"),
                    "direct_probe": direct_runtime,
                },
            }
    dependency_status = _overall_status(dependencies)
    queue_detail = dependencies.get("rabbitmq", {}).get("detail", {}).get("queues", {})
    dead_letter_count = sum(
        int(queue.get("dead_letter", 0))
        for queue in queue_detail.values()
        if isinstance(queue, dict)
    )
    pipeline_status = "degraded" if dead_letter_count else "healthy"
    return {
        # `status` remains the compact console summary. The two child
        # statuses prevent a reachable RabbitMQ broker from looking like a
        # healthy job pipeline when messages are accumulating in a DLQ.
        "status": dependency_status if dependency_status != "healthy" else pipeline_status,
        "dependency_status": dependency_status,
        "pipeline": {
            "status": pipeline_status,
            "detail": {"dead_letter_messages": dead_letter_count},
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dependencies": dependencies,
    }
