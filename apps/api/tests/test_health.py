from app import operational_health
from io import BytesIO
from urllib.error import HTTPError


def test_liveness_is_process_local(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_dependency_health_does_not_report_false_green(client, monkeypatch):
    monkeypatch.setattr(operational_health, "_check_database", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_rabbitmq", lambda: {"status": "unavailable"})
    monkeypatch.setattr(operational_health, "_check_minio", lambda: {"status": "healthy"})
    monkeypatch.setattr(
        operational_health,
        "_check_http_dependency",
        lambda _url, *, name: {"status": "healthy"},
    )
    response = client.get("/health/dependencies")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["dependencies"]["rabbitmq"]["status"] == "unavailable"


def test_hermes_health_reports_worker_separately_without_affecting_core_readiness(client, monkeypatch):
    monkeypatch.setattr(operational_health.settings, "ai_runtime", "hermes")
    monkeypatch.setattr(operational_health, "_check_database", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_rabbitmq", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_minio", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_hermes", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_ai_worker", lambda: {"status": "unavailable"})

    dependencies = client.get("/health/dependencies")
    assert dependencies.status_code == 200
    body = dependencies.json()
    assert body["dependencies"]["ai_runtime"]["status"] == "healthy"
    assert body["dependencies"]["ai_worker"]["status"] == "unavailable"
    assert body["status"] == "unavailable"

    readiness = client.get("/health/readiness")
    assert readiness.status_code == 200


def test_internal_hermes_is_reported_from_healthy_worker_probe(client, monkeypatch):
    """The host-run API must not require Hermes to be publicly published."""
    monkeypatch.setattr(operational_health.settings, "ai_runtime", "hermes")
    monkeypatch.setattr(operational_health, "_check_database", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_rabbitmq", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_minio", lambda: {"status": "healthy"})
    monkeypatch.setattr(
        operational_health,
        "_check_hermes",
        lambda: {"status": "unavailable", "detail": "hermes: URLError"},
    )
    monkeypatch.setattr(
        operational_health,
        "_check_ai_worker",
        lambda: {
            "status": "healthy",
            "detail": {
                "status": "healthy",
                "runtime": "hermes",
                "profile": "noc-log-analysis",
                "runtime_ready": True,
                "dependencies": {
                    "rabbitmq": "healthy",
                    "hermes": "healthy",
                    "hermes_profile": "healthy",
                    "postgresql": "healthy",
                    "minio": "healthy",
                },
            },
        },
    )

    response = client.get("/health/dependencies")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "healthy"
    assert body["dependencies"]["ai_runtime"]["status"] == "healthy"
    assert body["dependencies"]["ai_runtime"]["detail"]["source"] == "ai-worker"
    assert body["dependencies"]["ai_runtime"]["detail"]["direct_probe"]["status"] == "unavailable"


def test_worker_hermes_dependency_failure_is_not_hidden_by_ready_worker_status(monkeypatch):
    worker = {
        "status": "healthy",
        "detail": {
            "metrics": {"runtime_ready": True},
            "dependencies": {"hermes": "healthy", "hermes_profile": "unavailable"},
        },
    }
    assert not operational_health._worker_reports_hermes_healthy(worker)


def test_worker_readiness_degraded_for_database_does_not_hide_healthy_hermes():
    worker = {
        "status": "degraded",
        "detail": {
            "metrics": {"runtime_ready": True},
            "dependencies": {
                "hermes": "healthy",
                "hermes_profile": "healthy",
                "postgresql": "unavailable",
            },
        },
    }
    assert operational_health._worker_reports_hermes_healthy(worker)


def test_readiness_returns_service_unavailable_when_core_dependency_is_down(client, monkeypatch):
    monkeypatch.setattr(operational_health, "_check_database", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_rabbitmq", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_minio", lambda: {"status": "unavailable"})
    monkeypatch.setattr(
        operational_health,
        "_check_http_dependency",
        lambda _url, *, name: {"status": "healthy"},
    )
    response = client.get("/health/readiness")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_disabled_optional_ollama_is_not_unknown(client, monkeypatch):
    monkeypatch.setattr(operational_health.settings, "local_prefill_ai_enabled", False)
    monkeypatch.setattr(operational_health, "_check_database", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_rabbitmq", lambda: {"status": "healthy"})
    monkeypatch.setattr(operational_health, "_check_minio", lambda: {"status": "healthy"})
    monkeypatch.setattr(
        operational_health,
        "_check_http_dependency",
        lambda _url, *, name: {"status": "healthy"},
    )
    response = client.get("/health/dependencies")
    assert response.json()["status"] == "healthy"
    assert response.json()["dependencies"]["ollama"]["status"] == "disabled"


def test_reachable_rabbitmq_with_dlq_is_pipeline_degraded(client, monkeypatch):
    monkeypatch.setattr(operational_health, "_check_database", lambda: {"status": "healthy"})
    monkeypatch.setattr(
        operational_health,
        "_check_rabbitmq",
        lambda: {"status": "healthy", "detail": {"queues": {"log_triage": {"ready": 0, "dead_letter": 3}}}},
    )
    monkeypatch.setattr(operational_health, "_check_minio", lambda: {"status": "healthy"})
    monkeypatch.setattr(
        operational_health,
        "_check_http_dependency",
        lambda _url, *, name: {"status": "healthy"},
    )
    response = client.get("/health/dependencies")
    body = response.json()
    assert body["dependency_status"] == "healthy"
    assert body["pipeline"]["status"] == "degraded"
    assert body["status"] == "degraded"


def test_ollama_model_list_is_interpreted_as_health(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'{"models": [{"name": "qwen2.5:3b-instruct-q4_K_M"}]}'

    monkeypatch.setattr(operational_health.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(operational_health.settings, "local_prefill_model", "qwen2.5:3b-instruct-q4_K_M")
    assert operational_health._check_ollama()["status"] == "healthy"


def test_worker_readiness_http_503_preserves_degraded_payload(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise HTTPError(
            "http://worker/health/ready",
            503,
            "not ready",
            {},
            BytesIO(b'{"status":"degraded","component":"ai-worker"}'),
        )

    monkeypatch.setattr(operational_health.urllib.request, "urlopen", unavailable)
    result = operational_health._check_http_dependency("http://worker/health/ready", name="ai-worker")
    assert result == {
        "status": "degraded",
        "detail": {"status": "degraded", "component": "ai-worker"},
    }
