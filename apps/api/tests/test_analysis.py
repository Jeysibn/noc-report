"""Milestone 13 (Real Log Triage) — tests run against real Postgres,
RabbitMQ, and MinIO (same pattern as test_evidence.py/test_queue.py): no
mocked analyzer, no mocked broker/storage. The bridge/sandbox themselves
aren't started here (that's bridge/tests/test_bridge.py's job); this
suite verifies the API's own slice: it enqueues a real job and, once a
result artifact exists in noc-job-artifacts (simulating what the bridge
would have written), syncs it into the AnalysisRun row on poll.
"""
import json
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.queue import JOB_TYPES, _queue_names, declare_topology, get_connection
from app.core.storage import get_client

from tests.conftest import auth_headers, make_user


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


def _upload_log_evidence(client, headers, incident_id: str) -> dict:
    upload_req = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/upload-url",
        json={"evidence_type": "LOG", "filename": "app.log", "content_type": "text/plain"},
        headers=headers,
    )
    assert upload_req.status_code == 200
    body = upload_req.json()

    log_bytes = b"2026-09-09T00:00:00Z ERROR unhandled exception in request path\n"
    put_resp = httpx.put(body["upload_url"], content=log_bytes, headers={"Content-Type": "text/plain"})
    assert put_resp.status_code == 200

    complete_resp = client.post(
        f"/api/v1/incidents/{incident_id}/evidence/complete",
        json={
            "evidence_type": "LOG",
            "bucket": body["bucket"],
            "object_key": body["object_key"],
            "original_filename": "app.log",
            "mime_type": "text/plain",
        },
        headers=headers,
    )
    assert complete_resp.status_code == 201
    return complete_resp.json()


def test_request_analysis_requires_log_evidence(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)

    resp = client.post(
        f"/api/v1/incidents/{incident_id}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 409


def test_request_analysis_enqueues_real_job(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    _upload_log_evidence(client, headers, incident_id)

    connection = get_connection()
    channel = connection.channel()
    declare_topology(channel)
    _purge_all(channel)

    resp = client.post(
        f"/api/v1/incidents/{incident_id}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["status"] == "QUEUED"
    assert out["incident_id"] == incident_id
    assert out["result"] is None
    assert out["current"] is True
    job_id = out["job_id"]

    # No fallback analyzer in the API — the real message actually landed
    # on the real queue for a real bridge to pick up.
    names = _queue_names("log_triage")
    method, properties, body = channel.basic_get(names["main"], auto_ack=True)
    assert method is not None
    payload = json.loads(body)
    assert payload["job_id"] == job_id
    assert payload["incident_id"] == incident_id
    assert payload["object_refs"][0]["bucket"] == settings.minio_bucket_evidence
    connection.close()

    # Listing shows the same in-flight run, still no fabricated result.
    listed = client.get(f"/api/v1/incidents/{incident_id}/analysis-runs", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == "QUEUED"
    assert listed.json()[0]["result"] is None


def test_poll_syncs_result_once_job_completes(client, db_session):
    """Simulates what the bridge does on success (mark_completed + upload
    result.json to noc-job-artifacts) without actually running the
    bridge/sandbox — that pipeline is covered by bridge/tests/test_bridge.py."""
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    _upload_log_evidence(client, headers, incident_id)

    resp = client.post(
        f"/api/v1/incidents/{incident_id}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    from app.models.models import Job

    job = db_session.get(Job, __import__("uuid").UUID(job_id))
    job.status = "COMPLETED"
    job.completed_at = datetime.now(timezone.utc)
    db_session.commit()

    result = {
        "summary": "One error found in the log excerpt.",
        "likely_cause": "unhandled exception in request path",
        "severity_signal": "high",
        "recommended_action": "escalate to on-call engineer",
        "confidence": 0.87,
    }
    minio = get_client()
    minio.put_object(
        Bucket=settings.minio_bucket_job_artifacts,
        Key=f"jobs/{job_id}/result.json",
        Body=json.dumps(result).encode("utf-8"),
        ContentType="application/json",
    )

    polled = client.get(f"/api/v1/incidents/{incident_id}/analysis-runs/{job_id}", headers=headers)
    assert polled.status_code == 200
    out = polled.json()
    assert out["status"] == "COMPLETED"
    assert out["result"] == result
    assert out["run_id"] is not None

    # Second poll is a no-op re-fetch (result already synced), not a
    # second MinIO write attempt / overwrite.
    polled_again = client.get(f"/api/v1/incidents/{incident_id}/analysis-runs/{job_id}", headers=headers)
    assert polled_again.json()["result"] == result


def test_analysis_requires_permission(client, db_session):
    resp = client.post(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
    )
    assert resp.status_code == 401
