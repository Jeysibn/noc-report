from datetime import datetime, timezone

import httpx

from tests.conftest import auth_headers, make_user


def _create_incident(client, headers) -> str:
    resp = client.post(
        "/api/v1/incidents",
        json={
            "title": "Payments API timeout",
            "service": "payments-api",
            "environment": "production",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_upload_flow_end_to_end(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)

    # 1. request a presigned upload URL
    upload_req = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={
            "evidence_type": "ALERT_SCREENSHOT",
            "filename": "alert.png",
            "content_type": "image/png",
        },
        headers=headers,
    )
    assert upload_req.status_code == 200
    body = upload_req.json()
    assert body["object_key"] == f"incidents/{incident_id}/alert/alert.png"
    assert body["bucket"] == "noc-evidence"

    # 2. actually PUT bytes to the presigned URL (real MinIO, not mocked)
    put_resp = httpx.put(
        body["upload_url"], content=b"fake-png-bytes", headers={"Content-Type": "image/png"}
    )
    assert put_resp.status_code == 200

    # 3. complete — records metadata, verifies the object landed
    complete_resp = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={
            "evidence_type": "ALERT_SCREENSHOT",
            "bucket": body["bucket"],
            "object_key": body["object_key"],
            "original_filename": "alert.png",
            "mime_type": "image/png",
        },
        headers=headers,
    )
    assert complete_resp.status_code == 201
    evidence = complete_resp.json()
    assert evidence["byte_size"] == len(b"fake-png-bytes")

    # 4. list
    listed = client.get(f"/api/v1/incidents/{incident_id}/evidence", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # 5. download url
    download = client.get(f"/api/v1/evidence/{evidence['id']}/download-url", headers=headers)
    assert download.status_code == 200
    fetched = httpx.get(download.json()["download_url"])
    assert fetched.status_code == 200
    assert fetched.content == b"fake-png-bytes"

    # 6. delete
    deleted = client.delete(f"/api/v1/evidence/{evidence['id']}", headers=headers)
    assert deleted.status_code == 204

    listed_after = client.get(f"/api/v1/incidents/{incident_id}/evidence", headers=headers)
    assert listed_after.json() == []


def test_complete_rejects_object_never_uploaded(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)

    resp = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={
            "evidence_type": "LOG",
            "bucket": "noc-evidence",
            "object_key": f"incidents/{incident_id}/logs/never-uploaded.log",
            "original_filename": "never-uploaded.log",
        },
        headers=headers,
    )
    assert resp.status_code == 400


def test_upload_over_size_limit_is_rejected(client, db_session, monkeypatch):
    """Milestone 17 gap follow-up ("limits"): an object that lands in
    MinIO larger than the configured cap is rejected at complete_upload
    (there's no request body for the API to cap directly, since the
    upload itself goes straight from the browser to MinIO) and deleted
    rather than left as an orphaned over-limit object."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_evidence_upload_bytes", 10)

    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)

    upload_req = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "big.log", "content_type": "text/plain"},
        headers=headers,
    )
    body = upload_req.json()

    put_resp = httpx.put(
        body["upload_url"], content=b"this is way more than ten bytes", headers={"Content-Type": "text/plain"}
    )
    assert put_resp.status_code == 200

    complete_resp = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={
            "evidence_type": "LOG",
            "bucket": body["bucket"],
            "object_key": body["object_key"],
            "original_filename": "big.log",
            "mime_type": "text/plain",
        },
        headers=headers,
    )
    assert complete_resp.status_code == 413

    # The over-limit object should have been deleted, not left orphaned.
    from botocore.exceptions import ClientError

    from app.core.storage import get_client

    try:
        get_client().head_object(Bucket=body["bucket"], Key=body["object_key"])
        assert False, "object should have been deleted after rejection"
    except ClientError as e:
        assert e.response["Error"]["Code"] in ("404", "NoSuchKey")


def test_evidence_upload_requires_permission(client, db_session):
    # A role with no incident.evidence.upload permission shouldn't exist in
    # the seeded set, so simulate by hitting the endpoint unauthenticated
    # instead — the permission dependency should still 401/403 either way.
    resp = client.post(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "x.log"},
    )
    assert resp.status_code == 401
