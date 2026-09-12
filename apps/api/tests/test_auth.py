from app.models.models import AuditLog
from tests.conftest import make_user


def test_login_success(client, db_session):
    make_user(db_session, "operator1", "NOC")
    resp = client.post(
        "/api/v1/auth/login", json={"username": "operator1", "password": "pw123456"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_success_is_audited(client, db_session):
    """Milestone 17 (audit verification): a successful login is
    security-relevant and must leave an audit trail."""
    make_user(db_session, "operator1", "NOC")
    client.post("/api/v1/auth/login", json={"username": "operator1", "password": "pw123456"})

    db_session.expire_all()
    entries = db_session.query(AuditLog).filter(AuditLog.action == "auth.login").all()
    assert len(entries) == 1
    assert entries[0].resource_type == "user"


def test_login_wrong_password(client, db_session):
    make_user(db_session, "operator1", "NOC")
    resp = client.post(
        "/api/v1/auth/login", json={"username": "operator1", "password": "wrong"}
    )
    assert resp.status_code == 401


def test_login_failure_is_audited(client, db_session):
    """Milestone 17 (audit verification): a failed login attempt (wrong
    password or unknown user) must also leave an audit trail, keyed by
    the attempted username since there's no authenticated actor."""
    make_user(db_session, "operator1", "NOC")
    client.post("/api/v1/auth/login", json={"username": "operator1", "password": "wrong"})
    client.post("/api/v1/auth/login", json={"username": "nope", "password": "whatever"})

    db_session.expire_all()
    entries = db_session.query(AuditLog).filter(AuditLog.action == "auth.login.failed").all()
    assert {e.resource_id for e in entries} == {"operator1", "nope"}
    assert all(e.actor_user_id is None for e in entries)


def test_login_unknown_user(client, db_session):
    resp = client.post(
        "/api/v1/auth/login", json={"username": "nope", "password": "whatever"}
    )
    assert resp.status_code == 401


def test_refresh_and_me(client, db_session):
    make_user(db_session, "operator1", "NOC")
    login = client.post(
        "/api/v1/auth/login", json={"username": "operator1", "password": "pw123456"}
    )
    refresh_token = login.json()["refresh_token"]

    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    access_token = refreshed.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == "operator1"
    assert "NOC" in body["roles"]
    assert "incident.read" in body["permissions"]


def test_me_requires_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_logout_returns_no_content(client):
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
