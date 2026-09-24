"""Worker metrics plus process liveness and workload readiness endpoints."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urllib_request


class WorkerMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.values = {
            "jobs_queued": 0,
            "jobs_processing": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "retry_count": 0,
            "schema_validation_failures": 0,
            "provider_failures": 0,
            "last_duration_ms": 0,
            "last_preprocessing_duration_ms": 0,
            "queue_depth": 0,
            "runtime_ready": False,
        }

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self.values[name] = self.values.get(name, 0) + value

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.values)

    def set_runtime_ready(self, ready: bool) -> None:
        with self._lock:
            self.values["runtime_ready"] = ready

    def set_value(self, name: str, value) -> None:
        with self._lock:
            self.values[name] = value


def hermes_health_url(base_url: str) -> str:
    """Resolve a profile-scoped Hermes URL to the listener liveness endpoint."""
    listener_url = base_url.rstrip("/").split("/p/", 1)[0]
    return f"{listener_url}/health"


def hermes_is_reachable(base_url: str, timeout_seconds: float) -> bool:
    """Probe Hermes liveness without requiring provider credentials."""
    try:
        with urllib_request.urlopen(hermes_health_url(base_url), timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


class _HealthHandler(BaseHTTPRequestHandler):
    metrics: WorkerMetrics | None = None
    runtime_profile: str = "noc-log-analysis"
    runtime_base_url: str = "http://hermes:8642"
    runtime_health_timeout_seconds: float = 2.0
    worker_kind: str = "log_triage"

    def do_GET(self):  # noqa: N802 - stdlib HTTP handler API
        if self.path not in {"/health", "/health/live", "/health/ready", "/metrics"}:
            self.send_response(404)
            self.end_headers()
            return
        metrics = self.metrics.snapshot() if self.metrics else {}
        if self.path == "/health/live":
            payload = {"status": "healthy", "component": "ai-worker", "worker_kind": self.worker_kind}
            status_code = 200
        else:
            runtime_reachable = hermes_is_reachable(
                self.runtime_base_url,
                self.runtime_health_timeout_seconds,
            )
            ready = bool(metrics.get("runtime_ready", False) and runtime_reachable)
            metrics["runtime_reachable"] = runtime_reachable
            payload = {
                "status": "healthy" if ready else "degraded",
                "component": "ai-worker",
                "runtime": "hermes",
                "worker_kind": self.worker_kind,
                "profile": self.runtime_profile,
                "metrics": metrics,
            }
            status_code = 200 if ready or self.path in {"/health", "/metrics"} else 503
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def start_health_server(settings, metrics: WorkerMetrics):
    worker_kind = getattr(settings, "worker_kind", "log_triage")
    profile = (
        settings.hermes_profile
        if worker_kind == "log_triage"
        else settings.hermes_daily_report_profile
    )
    handler = type("WorkerHealthHandler", (_HealthHandler,), {
        "metrics": metrics,
        "worker_kind": worker_kind,
        "runtime_profile": profile,
        "runtime_base_url": settings.hermes_base_url,
        "runtime_health_timeout_seconds": min(5.0, max(0.5, settings.hermes_timeout_seconds)),
    })
    server = ThreadingHTTPServer((settings.health_host, settings.health_port), handler)
    thread = threading.Thread(target=server.serve_forever, name=f"{worker_kind}-health", daemon=True)
    thread.start()
    return server
