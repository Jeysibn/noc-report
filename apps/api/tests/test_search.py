"""Milestone 15 (Search / Knowledge) — real PostgreSQL FTS/trigram search,
no mocked search index."""
from datetime import datetime, timedelta, timezone

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


def test_search_requires_permission(client, db_session):
    resp = client.get("/api/v1/search")
    assert resp.status_code == 401


def test_search_full_text_matches_title(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    _create_incident(client, headers, title="Payments API timeout")
    _create_incident(client, headers, title="Database connection pool exhausted", service="billing-db")

    resp = client.get("/api/v1/search", params={"q": "payments"}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Payments API timeout"


def test_search_filters_by_service_and_environment(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    _create_incident(client, headers, service="payments-api", environment="production")
    _create_incident(client, headers, service="billing-db", environment="staging")

    resp = client.get("/api/v1/search", params={"service": "billing-db"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["service"] == "billing-db"

    resp2 = client.get("/api/v1/search", params={"environment": "staging"}, headers=headers)
    assert resp2.json()["total"] == 1


def test_search_date_range_and_recurrence_count(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    _create_incident(client, headers, title="Payments API timeout", triggered_at=old_time)
    _create_incident(client, headers, title="Payments API timeout")

    # Both share the same title -> recurrence_count should be 1 for each.
    resp = client.get("/api/v1/search", headers=headers)
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["recurrence_count"] == 1

    date_from = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp2 = client.get("/api/v1/search", params={"date_from": date_from}, headers=headers)
    assert resp2.json()["total"] == 1
