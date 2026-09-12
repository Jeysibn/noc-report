"""Milestone 16 (Analytics) — real PostgreSQL aggregates, no mocked
fixtures. `analytics.read` is DevOps/Admin only per seed.py."""
from datetime import datetime, timezone

from tests.conftest import auth_headers, make_user


def _create_incident(client, headers, **overrides) -> dict:
    body = {
        "title": "Payments API timeout",
        "service": "payments-api",
        "environment": "production",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }
    body.update(overrides)
    resp = client.post("/api/v1/incidents", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_analytics_requires_permission(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    resp = client.get("/api/v1/analytics/summary", headers=headers)
    assert resp.status_code == 403


def test_analytics_summary_shape_and_counts(client, db_session):
    make_user(db_session, "devops1", "DevOps")
    headers = auth_headers(client, "devops1")

    _create_incident(client, headers, title="Payments API timeout", service="payments-api")
    _create_incident(client, headers, title="Payments API timeout", service="payments-api")
    _create_incident(client, headers, title="Auth token validation failure", service="auth-service")

    resp = client.get("/api/v1/analytics/summary", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["incidents_by_day"]) == 8
    assert sum(row["value"] for row in body["incidents_by_day"]) == 3

    service_values = {row["label"]: row["value"] for row in body["alerts_by_service"]}
    assert service_values["payments-api"] == 2
    assert service_values["auth-service"] == 1

    recovered = {row["label"]: row["value"] for row in body["recovered_vs_unresolved"]}
    assert recovered["Open"] == 3

    top_titles = {row["title"]: row["count"] for row in body["top_recurring_alert_titles"]}
    assert top_titles["Payments API timeout"] == 2
    assert "Auth token validation failure" not in top_titles  # occurs once, not "recurring"

    assert body["report_generation_counts"] == {"this_shift": 0, "today": 0, "this_week": 0}
    assert body["analysis_job_outcomes"] == [
        {"label": "Completed", "value": 0},
        {"label": "Failed", "value": 0},
    ]
