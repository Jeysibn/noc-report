from datetime import datetime, timezone

from tests.conftest import auth_headers, make_user


def _incident_payload(**overrides):
    payload = {
        "title": "API gateway 5xx spike",
        "service": "api-gateway",
        "environment": "production",
        "alert_source": "grafana",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "trigger_value": "error_rate=12%",
    }
    payload.update(overrides)
    return payload


def test_create_and_get_incident(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    created = client.post(
        "/api/v1/incidents", json=_incident_payload(), headers=headers
    )
    assert created.status_code == 201
    body = created.json()
    assert body["display_id"].startswith("INC-")
    assert body["status"] == "open"

    fetched = client.get(f"/api/v1/incidents/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "API gateway 5xx spike"


def test_list_incidents_pagination(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    for i in range(3):
        client.post(
            "/api/v1/incidents",
            json=_incident_payload(title=f"Incident {i}"),
            headers=headers,
        )

    resp = client.get("/api/v1/incidents?limit=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_update_incident(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    created = client.post(
        "/api/v1/incidents", json=_incident_payload(), headers=headers
    ).json()

    updated = client.patch(
        f"/api/v1/incidents/{created['id']}",
        json={"status": "resolved"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "resolved"


def test_delete_incident_requires_delete_permission(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    created = client.post(
        "/api/v1/incidents", json=_incident_payload(), headers=headers
    ).json()

    # NOC role has no incident.delete permission
    resp = client.delete(f"/api/v1/incidents/{created['id']}", headers=headers)
    assert resp.status_code == 403


def test_admin_can_delete_incident(client, db_session):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    created = client.post(
        "/api/v1/incidents", json=_incident_payload(), headers=headers
    ).json()

    resp = client.delete(f"/api/v1/incidents/{created['id']}", headers=headers)
    assert resp.status_code == 204

    missing = client.get(f"/api/v1/incidents/{created['id']}", headers=headers)
    assert missing.status_code == 404


def test_get_nonexistent_incident_404(client, db_session):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert resp.status_code == 404
