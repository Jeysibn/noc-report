"""Worker metrics plus process liveness and workload readiness endpoints."""
from __future__ import annotations

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urllib_request

import boto3
import psycopg2
from botocore.client import Config as BotoConfig


PROBE_TIMEOUT_SECONDS = 1.0
PROBE_CACHE_TTL_SECONDS = 5.0


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
            "rabbitmq_ready": False,
            "hermes_profile_ready": False,
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


def hermes_profile_is_ready(settings, profile: str, timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Verify the assigned profile is routable and still satisfies NOC policy."""
    configured_url = settings.hermes_base_url.rstrip("/")
    if "/p/" in configured_url:
        listener_url, configured_profile = configured_url.split("/p/", 1)
        profile_url = configured_url if configured_profile == profile else f"{listener_url}/p/{profile}"
    else:
        profile_url = f"{configured_url}/p/{profile}"
    request = urllib_request.Request(
        f"{profile_url}/v1/toolsets",
        headers={
            "Authorization": f"Bearer {settings.hermes_api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        toolsets = payload.get("data")
        return isinstance(toolsets, list) and not any(
            isinstance(item, dict) and item.get("enabled") is True for item in toolsets
        )
    except Exception:
        return False


def postgresql_is_ready(database_url: str, timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Run a bounded, read-only probe against the worker's database."""
    connection = None
    try:
        timeout_ms = max(1, int(timeout_seconds * 1000))
        connection = psycopg2.connect(
            database_url,
            connect_timeout=max(1, int(timeout_seconds)),
            options=f"-c statement_timeout={timeout_ms}",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def minio_is_ready(settings, timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Check only the worker's bucket access; never enumerate or mutate objects."""
    buckets = [settings.minio_bucket_evidence, settings.minio_bucket_job_artifacts]
    if settings.worker_kind == "daily_report":
        buckets.append(settings.minio_bucket_reports)
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            config=BotoConfig(
                signature_version="s3v4",
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 0},
            ),
        )
        def head_bucket(bucket):
            try:
                client.head_bucket(Bucket=bucket)
                return True
            except Exception:
                return False

        # Buckets are independent. Parallel bounded HEADs keep daily-worker
        # readiness from taking three times the storage timeout on an outage.
        with ThreadPoolExecutor(max_workers=len(buckets)) as executor:
            return all(executor.map(head_bucket, buckets))
    except Exception:
        return False


class _ProbeCache:
    """Short cache prevents frequent container healthchecks from probing dependencies."""

    def __init__(self, ttl_seconds: float = PROBE_CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._values: dict[str, tuple[float, bool]] = {}

    def get(self, key: str, probe) -> bool:
        now = time.monotonic()
        with self._lock:
            cached = self._values.get(key)
            if cached and now - cached[0] < self.ttl_seconds:
                return cached[1]
        result = bool(probe())
        with self._lock:
            self._values[key] = (time.monotonic(), result)
            return result


class _HealthHandler(BaseHTTPRequestHandler):
    metrics: WorkerMetrics | None = None
    runtime_profile: str = "noc-log-analysis"
    runtime_base_url: str = "http://hermes:8642"
    runtime_health_timeout_seconds: float = 2.0
    worker_kind: str = "log_triage"
    settings = None
    probe_cache: _ProbeCache | None = None

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
            settings = self.settings
            cache = self.probe_cache or _ProbeCache()
            probes = {
                "hermes": lambda: cache.get(
                    "hermes", lambda: hermes_is_reachable(self.runtime_base_url, PROBE_TIMEOUT_SECONDS)
                ),
                "hermes_profile": lambda: cache.get(
                    "hermes_profile", lambda: hermes_profile_is_ready(settings, self.runtime_profile)
                ),
                "postgresql": lambda: cache.get(
                    "postgresql", lambda: postgresql_is_ready(settings.database_url)
                ),
                "minio": lambda: cache.get("minio", lambda: minio_is_ready(settings)),
            }
            with ThreadPoolExecutor(max_workers=len(probes)) as executor:
                probe_results = dict(zip(probes, executor.map(lambda probe: probe(), probes.values())))
            dependencies = {
                "rabbitmq": "healthy" if metrics.get("rabbitmq_ready", metrics.get("runtime_ready", False)) else "unavailable",
                **{name: "healthy" if result else "unavailable" for name, result in probe_results.items()},
            }
            if not metrics.get("hermes_profile_ready", metrics.get("runtime_ready", False)):
                dependencies["hermes_profile"] = "unavailable"
            ready = bool(metrics.get("runtime_ready", False) and all(value == "healthy" for value in dependencies.values()))
            metrics["dependencies"] = dependencies
            payload = {
                "status": "healthy" if ready else "degraded",
                "component": "ai-worker",
                "runtime": "hermes",
                "worker_kind": self.worker_kind,
                "profile": self.runtime_profile,
                "dependencies": dependencies,
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
    profile = settings.hermes_profile
    handler = type("WorkerHealthHandler", (_HealthHandler,), {
        "metrics": metrics,
        "worker_kind": worker_kind,
        "runtime_profile": profile,
        "runtime_base_url": settings.hermes_base_url,
        "runtime_health_timeout_seconds": PROBE_TIMEOUT_SECONDS,
        "settings": settings,
        "probe_cache": _ProbeCache(),
    })
    server = ThreadingHTTPServer((settings.health_host, settings.health_port), handler)
    thread = threading.Thread(target=server.serve_forever, name=f"{worker_kind}-health", daemon=True)
    thread.start()
    return server
