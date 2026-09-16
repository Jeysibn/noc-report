from app import operational_health


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
