"""Milestone 14 (Real Daily Report) — tests run against real Postgres,
RabbitMQ, and MinIO (same pattern as test_analysis.py): no mocked
generator, no mocked broker/storage. The bridge/sandbox themselves
aren't started here; this suite verifies the API's own slice: freezing a
real snapshot, enqueuing a real job, and, once a DOCX artifact exists in
noc-reports (simulating what the bridge would have written), syncing it
into the Report row on poll.
"""
import json
from datetime import datetime, timezone

from app.core.config import settings
from app.core.queue import JOB_TYPES, _queue_names, declare_topology, get_connection
from app.core.storage import get_client

from tests.conftest import auth_headers, make_user
from tests.test_shifts import _create_active_shift


def _purge_all(channel):
    for job_type in JOB_TYPES:
        names = _queue_names(job_type)
        channel.queue_purge(names["main"])
        channel.queue_purge(names["retry"])
        channel.queue_purge(names["dlq"])
    channel.queue_purge("noc.events.log")


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


def test_generate_report_requires_permission(client, db_session):
    shift = _create_active_shift(db_session)
    resp = client.post(
        f"/api/v1/shifts/{shift.id}/reports",
        json={"model": "claude-sonnet-5", "effort": "medium"},
    )
    assert resp.status_code == 401


def test_generate_report_freezes_snapshot_and_enqueues_real_job(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    shift = _create_active_shift(db_session)
    _create_incident(client, headers)  # auto-associated with the active shift

    connection = get_connection()
    channel = connection.channel()
    declare_topology(channel)
    _purge_all(channel)

    resp = client.post(
        f"/api/v1/shifts/{shift.id}/reports",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["status"] == "QUEUED"
    assert out["shift_id"] == str(shift.id)
    assert out["version"] == 1
    assert out["downloadable"] is False
    job_id = out["job_id"]

    # No fallback generator — the real message actually landed on the
    # real daily_report queue for a real bridge to pick up.
    names = _queue_names("daily_report")
    method, properties, body = channel.basic_get(names["main"], auto_ack=True)
    assert method is not None
    payload = json.loads(body)
    assert payload["job_id"] == job_id
    ref = payload["object_refs"][0]
    assert ref["bucket"] == settings.minio_bucket_reports

    # The frozen snapshot really is sitting in MinIO at that key, and it
    # contains the one incident created above.
    snapshot_bytes = get_client().get_object(Bucket=ref["bucket"], Key=ref["key"])["Body"].read()
    snapshot = json.loads(snapshot_bytes)
    assert snapshot["shift_id"] == str(shift.id)
    assert len(snapshot["incidents"]) == 1
    connection.close()

    # A second generate call for the same shift gets version 2, not a
    # second version 1.
    resp2 = client.post(
        f"/api/v1/shifts/{shift.id}/reports",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp2.json()["version"] == 2

    listed = client.get(f"/api/v1/shifts/{shift.id}/reports", headers=headers)
    assert listed.status_code == 200
    versions = [r["version"] for r in listed.json()]
    assert versions == [2, 1]


def test_poll_syncs_docx_once_job_completes_and_download_url_works(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    shift = _create_active_shift(db_session)

    resp = client.post(
        f"/api/v1/shifts/{shift.id}/reports",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 201
    report_id = resp.json()["id"]
    job_id = resp.json()["job_id"]

    # Not downloadable yet — bridge hasn't run.
    not_ready = client.get(f"/api/v1/reports/{report_id}/download-url", headers=headers)
    assert not_ready.status_code == 409

    from app.models.models import Job

    job = db_session.get(Job, __import__("uuid").UUID(job_id))
    job.status = "COMPLETED"
    job.completed_at = datetime.now(timezone.utc)
    db_session.commit()

    minio = get_client()
    minio.put_object(
        Bucket=settings.minio_bucket_reports,
        Key=f"reports/{job_id}/report.docx",
        Body=b"fake docx bytes for test",
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    listed = client.get(f"/api/v1/shifts/{shift.id}/reports", headers=headers)
    assert listed.status_code == 200
    out = listed.json()[0]
    assert out["status"] == "COMPLETED"
    assert out["downloadable"] is True

    download = client.get(f"/api/v1/reports/{report_id}/download-url", headers=headers)
    assert download.status_code == 200
    assert "download_url" in download.json()
