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
    assert body["upload_id"]
    assert "bucket" not in body
    assert "object_key" not in body

    # 2. actually PUT bytes to the presigned URL (real MinIO, not mocked)
    put_resp = httpx.put(
        body["upload_url"], content=b"fake-png-bytes", headers={"Content-Type": "image/png"}
    )
    assert put_resp.status_code == 200

    # 3. complete — records metadata, verifies the object landed
    complete_resp = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": body["upload_id"]},
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
        json={"upload_id": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert resp.status_code == 404


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
        json={"upload_id": body["upload_id"]},
        headers=headers,
    )
    assert complete_resp.status_code == 413

    # The over-limit object should have been deleted, not left orphaned.
    from botocore.exceptions import ClientError

    from app.core.storage import get_client

    try:
        from app.models.models import EvidenceUploadIntent

        intent = db_session.get(EvidenceUploadIntent, body["upload_id"])
        get_client().head_object(Bucket=intent.bucket, Key=intent.object_key)
        assert False, "object should have been deleted after rejection"
    except ClientError as e:
        assert e.response["Error"]["Code"] in ("404", "NoSuchKey")


def test_completion_rejects_client_storage_identity_fields(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    upload = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "a.log"},
        headers=headers,
    )
    assert upload.status_code == 200
    response = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={
            "upload_id": upload.json()["upload_id"],
            "bucket": "noc-reports",
            "object_key": "other-user/private.log",
        },
        headers=headers,
    )
    assert response.status_code == 422


def test_upload_intent_is_user_bound_and_completion_is_idempotent(client, db_session):
    make_user(db_session, "operator1", "NOC")
    make_user(db_session, "operator2", "NOC")
    owner_headers = auth_headers(client, "operator1")
    other_headers = auth_headers(client, "operator2")
    incident_id = _create_incident(client, owner_headers)
    upload = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "a.log", "content_type": "text/plain"},
        headers=owner_headers,
    ).json()
    assert client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": upload["upload_id"]},
        headers=other_headers,
    ).status_code == 403
    assert httpx.put(upload["upload_url"], content=b"hello", headers={"Content-Type": "text/plain"}).status_code == 200
    first = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": upload["upload_id"]},
        headers=owner_headers,
    )
    second = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": upload["upload_id"]},
        headers=owner_headers,
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_upload_intent_expiry_blocks_completion(client, db_session):
    from app.models.models import EvidenceUploadIntent

    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    upload = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "expired.log"},
        headers=headers,
    ).json()
    intent = db_session.get(EvidenceUploadIntent, upload["upload_id"])
    intent.expires_at = datetime.now(timezone.utc)
    db_session.commit()
    response = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": upload["upload_id"]},
        headers=headers,
    )
    assert response.status_code == 410


def test_download_is_pinned_to_completed_object_version(client, db_session):
    """Replacing a key after completion cannot change the evidence bytes
    returned to an operator: the Evidence row and presigned URL use the
    version captured by the upload intent."""
    from app.core.storage import get_client
    from app.models.models import Evidence

    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    upload = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={"evidence_type": "ALERT_SCREENSHOT", "filename": "alert.png", "content_type": "image/png"},
        headers=headers,
    ).json()
    assert httpx.put(upload["upload_url"], content=b"original", headers={"Content-Type": "image/png"}).status_code == 200
    evidence = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": upload["upload_id"]},
        headers=headers,
    ).json()
    stored = db_session.get(Evidence, evidence["id"])
    assert stored.version_id
    get_client().put_object(
        Bucket=stored.bucket,
        Key=stored.object_key,
        Body=b"replacement",
        ContentType="image/png",
    )
    download = client.get(f"/api/v1/evidence/{stored.id}/download-url", headers=headers)
    assert httpx.get(download.json()["download_url"]).content == b"original"


def test_completion_rejects_authoritative_size_and_mime_mismatches(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    upload = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={
            "evidence_type": "LOG",
            "filename": "log.txt",
            "content_type": "text/plain",
            "expected_byte_size": 10,
        },
        headers=headers,
    ).json()
    # The presigned URL also binds the expected content type, so a browser
    # attempting to alter it is rejected before completion can be called.
    assert httpx.put(upload["upload_url"], content=b"wrong", headers={"Content-Type": "application/json"}).status_code == 403
    response = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={"upload_id": upload["upload_id"]},
        headers=headers,
    )
    assert response.status_code == 400


def test_evidence_upload_requires_permission(client, db_session):
    # A role with no incident.evidence.upload permission shouldn't exist in
    # the seeded set, so simulate by hitting the endpoint unauthenticated
    # instead — the permission dependency should still 401/403 either way.
    resp = client.post(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "x.log"},
    )
    assert resp.status_code == 401
