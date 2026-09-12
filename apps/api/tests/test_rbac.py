from tests.conftest import auth_headers, make_user


def test_noc_role_cannot_manage_users(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 403


def test_admin_role_can_manage_users(client, db_session):
    make_user(db_session, "root", "Admin")
    headers = auth_headers(client, "root")

    resp = client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 200


def test_devops_role_can_read_analytics_permission_but_not_manage_users(client, db_session):
    make_user(db_session, "devops1", "DevOps")
    headers = auth_headers(client, "devops1")

    me = client.get("/api/v1/auth/me", headers=headers)
    assert "analytics.read" in me.json()["permissions"]

    resp = client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 403
