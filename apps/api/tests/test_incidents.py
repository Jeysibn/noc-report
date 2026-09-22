import uuid
from datetime import datetime, timezone

from app.models.models import Evidence, Incident, Job, OutboxEvent, Shift, ShiftDefinition
from tests.conftest import auth_headers, make_user
from tests.test_shifts import _create_active_shift


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


def test_list_incidents_can_use_the_canonical_shift_scope(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    definition = ShiftDefinition(name="Day", start_time="06:00:00", end_time="14:00:00", timezone="Asia/Manila")
    other_definition = ShiftDefinition(name="Night", start_time="22:00:00", end_time="06:00:00", timezone="Asia/Manila")
    db_session.add_all([definition, other_definition])
    db_session.flush()
    shift_a = Shift(shift_definition_id=definition.id, starts_at=datetime.now(timezone.utc), state="active")
    shift_b = Shift(shift_definition_id=other_definition.id, starts_at=datetime.now(timezone.utc), state="ended")
    db_session.add_all([shift_a, shift_b])
    db_session.flush()
    for i in range(14):
        db_session.add(Incident(
            display_id=f"INC-A-{i}", shift_id=shift_a.id, title=f"A-{i}", service="api",
            environment="production", status="open", triggered_at=datetime.now(timezone.utc),
        ))
    for i in range(6):
        db_session.add(Incident(
            display_id=f"INC-B-{i}", shift_id=shift_b.id, title=f"B-{i}", service="api",
            environment="production", status="open", triggered_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    response = client.get(f"/api/v1/incidents?shift_id={shift_a.id}&limit=0", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 14
    assert len(body["items"]) == 14
    assert all(item["shift_id"] == str(shift_a.id) for item in body["items"])


def test_incident_readiness_is_a_narrow_shift_projection(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    shift = _create_active_shift(db_session)
    db_session.add(Incident(
        display_id="INC-READINESS-1",
        shift_id=shift.id,
        title="Readiness projection",
        service="api",
        environment="production",
        status="open",
        triggered_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    response = client.get(f"/api/v1/incidents/readiness?shift_id={shift.id}", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["title"] == "Readiness projection"
    assert payload[0]["has_log"] is False
    assert payload[0]["analysis_status"] == "not_analyzed"
    assert set(payload[0]) == {"id", "title", "has_log", "analysis_status"}


def test_update_incident(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    created = client.post(
        "/api/v1/incidents", json=_incident_payload(), headers=headers
    ).json()

    updated = client.patch(
        f"/api/v1/incidents/{created['id']}",
        json={"status": "recovered", "recovered_at": datetime.now(timezone.utc).isoformat()},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "recovered"


def test_recovery_status_requires_recovery_timestamp(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    response = client.post(
        "/api/v1/incidents",
        json=_incident_payload(status="recovered"),
        headers=headers,
    )
    assert response.status_code == 422


def test_non_recovered_status_rejects_recovery_timestamp(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    response = client.post(
        "/api/v1/incidents",
        json=_incident_payload(recovered_at=datetime.now(timezone.utc).isoformat()),
        headers=headers,
    )
    assert response.status_code == 422


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


def test_admin_can_delete_incident_whose_job_has_an_outbox_event(client, db_session):
    """Regression test: a real job (ADR 0006's outbox pattern) always has
    at least one outbox_events row referencing it. delete_incident must
    clear those rows before deleting the Job, or Postgres rejects the
    delete with a ForeignKeyViolation — which the frontend surfaced to
    the user as an opaque "Failed to fetch"."""
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    created = client.post(
        "/api/v1/incidents", json=_incident_payload(), headers=headers
    ).json()
    incident_id = created["id"]

    from app.skills.registry import resolve_active_snapshot

    skill_snapshot = resolve_active_snapshot(db_session, "log-triage-summary")
    job = Job(
        job_type="log_triage",
        status="QUEUED",
        incident_id=uuid.UUID(incident_id),
        skill_name="log-triage-summary",
        skill_version="1",
        skill_hash=skill_snapshot.content_hash,
        skill_snapshot_id=skill_snapshot.id,
        correlation_id="test-corr-id",
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        OutboxEvent(
            event_type="job.dispatch",
            aggregate_type="job",
            aggregate_id=job.id,
            job_id=job.id,
            routing_key="noc.jobs.log-triage",
            payload={},
        )
    )
    db_session.commit()

    resp = client.delete(f"/api/v1/incidents/{incident_id}", headers=headers)
    assert resp.status_code == 204

    missing = client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
    assert missing.status_code == 404


def test_get_nonexistent_incident_404(client, db_session):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert resp.status_code == 404


def test_list_incidents_has_log_filters_active_log_evidence(client, db_session):
    make_user(db_session, "filter-operator", "NOC")
    headers = auth_headers(client, "filter-operator")
    with_log = client.post("/api/v1/incidents", json=_incident_payload(title="With log"), headers=headers).json()
    screenshot_only = client.post("/api/v1/incidents", json=_incident_payload(title="Screenshot only"), headers=headers).json()
    removed_log = client.post("/api/v1/incidents", json=_incident_payload(title="Removed log"), headers=headers).json()
    db_session.add_all([
        Evidence(
            incident_id=uuid.UUID(with_log["id"]), evidence_type="LOG", bucket="noc-evidence",
            object_key="with.log", original_filename="with.log", lifecycle_state="ACTIVE",
        ),
        Evidence(
            incident_id=uuid.UUID(screenshot_only["id"]), evidence_type="ALERT_SCREENSHOT", bucket="noc-evidence",
            object_key="shot.png", original_filename="shot.png", lifecycle_state="ACTIVE",
        ),
        Evidence(
            incident_id=uuid.UUID(removed_log["id"]), evidence_type="LOG", bucket="noc-evidence",
            object_key="removed.log", original_filename="removed.log", lifecycle_state="PURGED",
        ),
    ])
    db_session.commit()

    yes = client.get("/api/v1/incidents?has_log=true", headers=headers)
    no = client.get("/api/v1/incidents?has_log=false", headers=headers)
    assert yes.status_code == no.status_code == 200
    assert {item["title"] for item in yes.json()["items"]} == {"With log"}
    assert {item["title"] for item in no.json()["items"]} == {"Screenshot only", "Removed log"}
