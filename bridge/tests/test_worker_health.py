import json
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from noc_bridge import worker as worker_module
from noc_bridge import worker_health
from noc_bridge.config import RuntimeSettings


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_hermes_health_url_strips_profile_path():
    assert worker_health.hermes_health_url("http://hermes:8642/p/noc-log-analysis") == "http://hermes:8642/health"


def test_hermes_liveness_probe_reports_reachable(monkeypatch):
    monkeypatch.setattr(worker_health.urllib_request, "urlopen", lambda *_args, **_kwargs: _Response())
    assert worker_health.hermes_is_reachable("http://hermes:8642/p/noc-log-analysis", 2.0) is True


def test_hermes_liveness_probe_reports_outage(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(worker_health.urllib_request, "urlopen", unavailable)
    assert worker_health.hermes_is_reachable("http://hermes:8642/p/noc-log-analysis", 2.0) is False


def test_worker_health_separates_liveness_from_readiness(monkeypatch):
    monkeypatch.setattr(worker_health, "hermes_is_reachable", lambda *_: False)
    monkeypatch.setattr(worker_health, "hermes_profile_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "postgresql_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "minio_is_ready", lambda *_: True)
    settings = RuntimeSettings(health_host="127.0.0.1", health_port=0)
    metrics = worker_health.WorkerMetrics()
    server = worker_health.start_health_server(settings, metrics)
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read())["status"] == "healthy"
        try:
            urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=2)
        except HTTPError as exc:
            assert exc.code == 503
            payload = json.loads(exc.read())
            assert payload["status"] == "degraded"
            assert payload["dependencies"]["hermes"] == "unavailable"
        else:
            raise AssertionError("unready worker returned success")
    finally:
        server.shutdown()
        server.server_close()


def _health_payload(server, path):
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}{path}", timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_readiness_is_healthy_when_all_workload_dependencies_are_healthy(monkeypatch):
    monkeypatch.setattr(worker_health, "hermes_is_reachable", lambda *_: True)
    monkeypatch.setattr(worker_health, "hermes_profile_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "postgresql_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "minio_is_ready", lambda *_: True)
    settings = RuntimeSettings(health_host="127.0.0.1", health_port=0)
    metrics = worker_health.WorkerMetrics()
    metrics.set_runtime_ready(True)
    metrics.set_value("rabbitmq_ready", True)
    metrics.set_value("hermes_profile_ready", True)
    server = worker_health.start_health_server(settings, metrics)
    try:
        status, payload = _health_payload(server, "/health/ready")
        assert status == 200
        assert set(payload["dependencies"].values()) == {"healthy"}
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("dependency,probe", [
    ("postgresql", "postgresql_is_ready"),
    ("minio", "minio_is_ready"),
])
def test_dependency_failure_fails_readiness_but_not_liveness(monkeypatch, dependency, probe):
    monkeypatch.setattr(worker_health, "hermes_is_reachable", lambda *_: True)
    monkeypatch.setattr(worker_health, "hermes_profile_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "postgresql_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "minio_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, probe, lambda *_: False)
    settings = RuntimeSettings(health_host="127.0.0.1", health_port=0)
    metrics = worker_health.WorkerMetrics()
    metrics.set_runtime_ready(True)
    metrics.set_value("rabbitmq_ready", True)
    metrics.set_value("hermes_profile_ready", True)
    server = worker_health.start_health_server(settings, metrics)
    try:
        live_status, live_payload = _health_payload(server, "/health/live")
        ready_status, ready_payload = _health_payload(server, "/health/ready")
        assert live_status == 200
        assert live_payload["status"] == "healthy"
        assert ready_status == 503
        assert ready_payload["dependencies"][dependency] == "unavailable"
        assert ready_payload["dependencies"]["rabbitmq"] == "healthy"
        assert ready_payload["dependencies"]["hermes"] == "healthy"
    finally:
        server.shutdown()
        server.server_close()


def test_hermes_profile_toolset_probe_rejects_policy_violation(monkeypatch):
    class Response(_Response):
        def read(self):
            return b'{"data":[{"name":"browser","enabled":true}]}'

    settings = RuntimeSettings(
        hermes_base_url="http://hermes:8642/p/noc-log-analysis",
        hermes_api_key="safe-test-token",
    )

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://hermes:8642/p/noc-log-analysis/v1/toolsets"
        assert request.get_header("Authorization") == "Bearer safe-test-token"
        assert timeout <= 2
        return Response()

    monkeypatch.setattr(worker_health.urllib_request, "urlopen", fake_urlopen)
    assert not worker_health.hermes_profile_is_ready(settings, "noc-log-analysis")


def test_broken_assigned_profile_fails_readiness_when_hermes_listener_is_healthy(monkeypatch):
    monkeypatch.setattr(worker_health, "hermes_is_reachable", lambda *_: True)
    monkeypatch.setattr(worker_health, "hermes_profile_is_ready", lambda *_: False)
    monkeypatch.setattr(worker_health, "postgresql_is_ready", lambda *_: True)
    monkeypatch.setattr(worker_health, "minio_is_ready", lambda *_: True)
    settings = RuntimeSettings(health_host="127.0.0.1", health_port=0)
    metrics = worker_health.WorkerMetrics()
    metrics.set_runtime_ready(True)
    metrics.set_value("rabbitmq_ready", True)
    metrics.set_value("hermes_profile_ready", True)
    server = worker_health.start_health_server(settings, metrics)
    try:
        status, payload = _health_payload(server, "/health/ready")
        assert status == 503
        assert payload["dependencies"]["hermes"] == "healthy"
        assert payload["dependencies"]["hermes_profile"] == "unavailable"
    finally:
        server.shutdown()
        server.server_close()


def test_minio_probe_checks_worker_buckets_without_listing(monkeypatch):
    calls = []
    all_started = threading.Barrier(3)

    class Client:
        def head_bucket(self, **kwargs):
            calls.append(("head", kwargs["Bucket"]))
            all_started.wait(timeout=0.5)

        def list_objects_v2(self, **_kwargs):
            raise AssertionError("readiness must not enumerate objects")

    monkeypatch.setattr(worker_health.boto3, "client", lambda *_args, **_kwargs: Client())
    settings = RuntimeSettings(worker_kind="daily_report", hermes_profile="noc-daily-report")
    assert worker_health.minio_is_ready(settings)
    assert {bucket for operation, bucket in calls if operation == "head"} == {
        settings.minio_bucket_evidence,
        settings.minio_bucket_job_artifacts,
        settings.minio_bucket_reports,
    }


def test_cold_readiness_probes_run_in_parallel_and_are_bounded(monkeypatch):
    def slow_probe(*_args):
        time.sleep(0.2)
        return True

    monkeypatch.setattr(worker_health, "hermes_is_reachable", slow_probe)
    monkeypatch.setattr(worker_health, "hermes_profile_is_ready", slow_probe)
    monkeypatch.setattr(worker_health, "postgresql_is_ready", slow_probe)
    monkeypatch.setattr(worker_health, "minio_is_ready", slow_probe)
    settings = RuntimeSettings(health_host="127.0.0.1", health_port=0)
    metrics = worker_health.WorkerMetrics()
    metrics.set_runtime_ready(True)
    metrics.set_value("rabbitmq_ready", True)
    metrics.set_value("hermes_profile_ready", True)
    server = worker_health.start_health_server(settings, metrics)
    try:
        started = time.monotonic()
        status, _payload = _health_payload(server, "/health/ready")
        elapsed = time.monotonic() - started
        assert status == 200
        assert elapsed < 0.8
        assert worker_health.PROBE_TIMEOUT_SECONDS <= 1.0
        assert worker_health.PROBE_CACHE_TTL_SECONDS <= 5.0
    finally:
        server.shutdown()
        server.server_close()


def test_postgresql_probe_uses_read_only_select_and_closes_connection(monkeypatch):
    seen = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql):
            seen["sql"] = sql

        def fetchone(self):
            return (1,)

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(worker_health.psycopg2, "connect", lambda *args, **kwargs: (seen.update(kwargs=kwargs) or connection))
    assert worker_health.postgresql_is_ready("postgresql://user:secret@db/noc")
    assert seen["sql"] == "SELECT 1"
    assert seen["kwargs"]["connect_timeout"] <= 2
    assert connection.closed


def test_log_worker_does_not_validate_daily_profile(monkeypatch):
    consumed = []
    verified = []

    class Channel:
        def queue_declare(self, *_args, **_kwargs):
            return SimpleNamespace(method=SimpleNamespace(message_count=0))

        def basic_qos(self, **_kwargs):
            return None

        def basic_consume(self, queue, _callback, **_kwargs):
            consumed.append(queue)

        def start_consuming(self):
            raise KeyboardInterrupt

    class Connection:
        is_closed = False

        def channel(self):
            return Channel()

        def close(self):
            self.is_closed = True

    class FakeHermes:
        def __init__(self, _settings, *, profile):
            self.profile = profile

        def verify_restricted_toolsets(self):
            verified.append(self.profile)
            if self.profile == "noc-daily-report":
                raise RuntimeError("daily profile is broken")

    monkeypatch.setattr(worker_module, "get_client", lambda _settings: object())
    monkeypatch.setattr(worker_module, "get_rabbit_connection", lambda _url: Connection())
    monkeypatch.setattr(worker_module, "declare_topology", lambda _channel: None)
    monkeypatch.setattr(worker_module, "HermesClient", FakeHermes)
    settings = RuntimeSettings(worker_kind="log_triage")
    worker_module.Worker(settings).run_forever()

    assert verified == ["noc-log-analysis"]
    assert consumed == ["noc.jobs.log-triage"]
