from tests.conftest import auth_headers, make_user


def test_create_user_and_list(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.post(
        "/api/v1/admin/users",
        json={
            "username": "newnoc",
            "display_name": "New NOC Operator",
            "password": "pw123456",
            "role_names": ["NOC"],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["roles"] == ["NOC"]

    listed = client.get("/api/v1/admin/users", headers=headers)
    assert listed.status_code == 200
    usernames = [u["username"] for u in listed.json()]
    assert "newnoc" in usernames
    assert "root" in usernames


def test_create_user_duplicate_username_conflicts(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    client.post(
        "/api/v1/admin/users",
        json={
            "username": "dupe",
            "display_name": "Dupe",
            "password": "pw123456",
            "role_names": [],
        },
        headers=headers,
    )
    resp = client.post(
        "/api/v1/admin/users",
        json={
            "username": "dupe",
            "display_name": "Dupe Again",
            "password": "pw123456",
            "role_names": [],
        },
        headers=headers,
    )
    assert resp.status_code == 409


def test_update_user_roles(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "promoteme",
            "display_name": "Promote Me",
            "password": "pw123456",
            "role_names": ["NOC"],
        },
        headers=headers,
    ).json()

    updated = client.patch(
        f"/api/v1/admin/users/{created['id']}",
        json={"role_names": ["DevOps"]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["roles"] == ["DevOps"]


def test_list_roles(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get("/api/v1/admin/roles", headers=headers)
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert names == {"NOC", "DevOps", "Admin"}


def test_update_non_admin_role_permissions(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")
    roles = client.get("/api/v1/admin/roles", headers=headers).json()
    noc = next(role for role in roles if role["name"] == "NOC")

    resp = client.patch(
        f"/api/v1/admin/roles/{noc['id']}",
        json={"permissions": ["dashboard.read", "incident.read"]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["permissions"] == ["dashboard.read", "incident.read"]


def test_administrator_role_is_immutable(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")
    roles = client.get("/api/v1/admin/roles", headers=headers).json()
    admin = next(role for role in roles if role["name"] == "Admin")

    resp = client.patch(
        f"/api/v1/admin/roles/{admin['id']}",
        json={"permissions": ["dashboard.read"]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "cannot be edited" in resp.json()["detail"]


def test_audit_log_records_incident_create(client, db_session):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    client.post(
        "/api/v1/incidents",
        json={
            "title": "test",
            "service": "svc",
            "environment": "production",
            "triggered_at": "2026-01-01T00:00:00Z",
        },
        headers=headers,
    )

    resp = client.get("/api/v1/admin/audit", headers=headers)
    assert resp.status_code == 200
    actions = [entry["action"] for entry in resp.json()]
    assert "incident.create" in actions


def test_list_shift_definitions_seeded(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get("/api/v1/admin/shift-definitions", headers=headers)
    assert resp.status_code == 200
    names = {d["name"] for d in resp.json()}
    assert names == {"Day", "Swing", "Night"}


def test_update_shift_definition_requires_permission(client, db_session, seeded):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.get("/api/v1/admin/shift-definitions", headers=headers)
    definition_id = resp.json()[0]["id"]

    resp = client.patch(
        f"/api/v1/admin/shift-definitions/{definition_id}",
        json={"enabled": False},
        headers=headers,
    )
    assert resp.status_code == 403


def test_update_shift_definition(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get("/api/v1/admin/shift-definitions", headers=headers)
    day = next(d for d in resp.json() if d["name"] == "Day")

    resp = client.patch(
        f"/api/v1/admin/shift-definitions/{day['id']}",
        json={"start_time": "07:00:00", "end_time": "15:00:00"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["start_time"] == "07:00:00"
    assert body["end_time"] == "15:00:00"
    assert body["enabled"] is True  # unset fields untouched

    audit = client.get("/api/v1/admin/audit", headers=headers).json()
    assert any(e["action"] == "shift_definition.update" for e in audit)


def test_storage_status(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get("/api/v1/admin/storage", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    buckets = {b["bucket"] for b in body}
    assert {"noc-evidence", "noc-reports", "noc-job-artifacts"} <= buckets
    versioned = {b["bucket"]: b["versioning_enabled"] for b in body}
    assert versioned["noc-evidence"] is True
    assert versioned["noc-job-artifacts"] is False
    for b in body:
        assert b["object_count"] >= 0
        assert b["total_bytes"] >= 0


def test_storage_status_requires_permission(client, db_session, seeded):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.get("/api/v1/admin/storage", headers=headers)
    assert resp.status_code == 403


def test_get_system_config_seeded_defaults(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get("/api/v1/admin/system-config", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_model"] == "claude-sonnet-5"
    assert body["default_effort"] == "low"
    assert body["job_timeout_seconds"] == 300
    assert body["max_concurrent_jobs"] == 1


def test_update_system_config_requires_permission(client, db_session, seeded):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.patch(
        "/api/v1/admin/system-config", json={"default_effort": "high"}, headers=headers
    )
    assert resp.status_code == 403


def test_update_system_config(client, db_session, seeded):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.patch(
        "/api/v1/admin/system-config",
        json={"default_effort": "high", "max_concurrent_jobs": 4},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_effort"] == "high"
    assert body["max_concurrent_jobs"] == 4
    assert body["default_model"] == "claude-sonnet-5"  # unset fields untouched

    audit = client.get("/api/v1/admin/audit", headers=headers).json()
    assert any(e["action"] == "system_config.update" for e in audit)
