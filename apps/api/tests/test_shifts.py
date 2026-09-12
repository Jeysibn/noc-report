from datetime import datetime, timezone

from tests.conftest import auth_headers, make_user
from app.models.models import Shift, ShiftDefinition


def _create_active_shift(db_session) -> Shift:
    definition = ShiftDefinition(
        name="Day", start_time="06:00:00", end_time="14:00:00", timezone="Asia/Manila"
    )
    db_session.add(definition)
    db_session.flush()
    shift = Shift(
        shift_definition_id=definition.id,
        starts_at=datetime.now(timezone.utc),
        state="active",
    )
    db_session.add(shift)
    db_session.commit()
    return shift


def test_no_active_shift_returns_404(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.get("/api/v1/shifts/current", headers=headers)
    assert resp.status_code == 404


def test_get_current_shift(client, db_session):
    make_user(db_session, "operator1", "NOC")
    shift = _create_active_shift(db_session)
    headers = auth_headers(client, "operator1")

    resp = client.get("/api/v1/shifts/current", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == str(shift.id)


def test_noc_can_close_shift(client, db_session):
    make_user(db_session, "operator1", "NOC")
    shift = _create_active_shift(db_session)
    headers = auth_headers(client, "operator1")

    resp = client.post(f"/api/v1/shifts/{shift.id}/close", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["state"] == "ended"


def test_noc_can_open_shift(client, db_session):
    make_user(db_session, "operator1", "NOC")
    definition = ShiftDefinition(
        name="Day", start_time="08:00:00", end_time="16:00:00", timezone="Asia/Manila"
    )
    db_session.add(definition)
    db_session.commit()
    headers = auth_headers(client, "operator1")

    resp = client.post("/api/v1/shifts/open", json={}, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["state"] == "active"


def test_admin_can_close_shift(client, db_session):
    make_user(db_session, "root", "Admin")
    shift = _create_active_shift(db_session)
    headers = auth_headers(client, "root")

    resp = client.post(f"/api/v1/shifts/{shift.id}/close", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["state"] == "ended"

    # Closing again should fail because it's no longer active
    resp2 = client.post(f"/api/v1/shifts/{shift.id}/close", headers=headers)
    assert resp2.status_code == 400
