from noc_bridge import worker as worker_module


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_hermes_health_url_strips_profile_path():
    assert worker_module.hermes_health_url("http://hermes:8642/p/noc-log-analysis") == "http://hermes:8642/health"


def test_hermes_liveness_probe_reports_reachable(monkeypatch):
    monkeypatch.setattr(worker_module.urllib_request, "urlopen", lambda *_args, **_kwargs: _Response())
    assert worker_module.hermes_is_reachable("http://hermes:8642/p/noc-log-analysis", 2.0) is True


def test_hermes_liveness_probe_reports_outage(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(worker_module.urllib_request, "urlopen", unavailable)
    assert worker_module.hermes_is_reachable("http://hermes:8642/p/noc-log-analysis", 2.0) is False
