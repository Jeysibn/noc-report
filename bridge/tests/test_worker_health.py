import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

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
            assert json.loads(exc.read())["status"] == "degraded"
        else:
            raise AssertionError("unready worker returned success")
    finally:
        server.shutdown()
        server.server_close()


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
