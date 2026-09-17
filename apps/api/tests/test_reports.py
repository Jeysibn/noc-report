"""Milestone 14 (Real Daily Report) — tests run against real Postgres,
RabbitMQ, and MinIO (same pattern as test_analysis.py): no mocked
generator, no mocked broker/storage. The bridge/sandbox themselves
aren't started here; this suite verifies the API's own slice: freezing a
real snapshot, enqueuing a real job, and, once a DOCX artifact exists in
noc-reports (simulating what the bridge would have written), syncing it
into the Report row on poll.
"""
import json
import time
from datetime import datetime, timezone

from app.core.config import settings
from app.core.queue import JOB_TYPES, _queue_names, declare_topology, get_connection
from app.core.storage import get_client, sha256_of_bytes

from tests.conftest import auth_headers, make_user
from tests.test_shifts import _create_active_shift
from app.models.models import Incident, Shift, ShiftDefinition
from app.models.models import Evidence


def _purge_all(channel):
    for job_type in JOB_TYPES:
        names = _queue_names(job_type)
        channel.queue_purge(names["main"])
        channel.queue_purge(names["retry"])
        channel.queue_purge(names["dlq"])
    channel.queue_purge("noc.events.log")


def _create_incident(client, headers, *, with_report_links: bool = False) -> str:
    resp = client.post(
        "/api/v1/incidents",
        json={
            "title": "Payments API timeout",
            "service": "payments-api",
            "environment": "production",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "trigger_value": "95%" if with_report_links else None,
            "teams_url": "https://teams.example/inc-001" if with_report_links else None,
            "grafana_url": "https://grafana.example/inc-001" if with_report_links else None,
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
    _create_incident(client, headers, with_report_links=True)  # auto-associated with the active shift

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
    # real daily_report queue for a real bridge to pick up. Publishing is
    # now deferred to the outbox dispatcher background thread
    # (Reliability mission Batch A), so poll for it instead of assuming
    # it's there the instant the request returns.
    names = _queue_names("daily_report")
    method = properties = body = None
    for _ in range(50):
        method, properties, body = channel.basic_get(names["main"], auto_ack=True)
        if method is not None:
            break
        time.sleep(0.2)
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
    assert snapshot["shift_timezone"] == shift.definition.timezone
    frozen_timezone = snapshot["shift_timezone"]
    shift.definition.timezone = "UTC"
    assert frozen_timezone == "Asia/Manila"
    assert len(snapshot["incidents"]) == 1
    assert snapshot["incidents"][0]["trigger_value"] == "95%"
    assert snapshot["incidents"][0]["teams_url"] == "https://teams.example/inc-001"
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


def test_readiness_scope_and_report_snapshot_use_the_same_shift_incidents(client, db_session):
    """The readiness query and the report freeze must agree even when
    another shift has more recent incidents and the selected shift has more
    than the old ten-row UI limit."""
    make_user(db_session, "operator-scope", "NOC")
    headers = auth_headers(client, "operator-scope")
    shift_a = _create_active_shift(db_session)
    definition_b = ShiftDefinition(name="Night", start_time="22:00:00", end_time="06:00:00", timezone="Asia/Manila")
    db_session.add(definition_b)
    db_session.flush()
    shift_b = Shift(shift_definition_id=definition_b.id, starts_at=datetime.now(timezone.utc), state="ended")
    db_session.add(shift_b)
    db_session.flush()
    for i in range(14):
        db_session.add(Incident(
            display_id=f"INC-SCOPE-A-{i}", shift_id=shift_a.id, title=f"A-{i}", service="api",
            environment="production", status="open", triggered_at=datetime.now(timezone.utc),
        ))
    for i in range(6):
        db_session.add(Incident(
            display_id=f"INC-SCOPE-B-{i}", shift_id=shift_b.id, title=f"B-{i}", service="api",
            environment="production", status="open", triggered_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    readiness = client.get(f"/api/v1/incidents?shift_id={shift_a.id}&limit=0", headers=headers).json()
    readiness_ids = {item["id"] for item in readiness["items"]}
    assert readiness["total"] == 14

    from app.api.v1.routers.reports import _build_snapshot
    from app.skills.registry import resolve_active_snapshot
    report_snapshot = _build_snapshot(db_session, shift_a, resolve_active_snapshot(db_session, "daily-alert-report"))
    assert {item["id"] for item in report_snapshot["incidents"]} == readiness_ids


def test_report_snapshot_carries_per_incident_analysis_provenance(client, db_session):
    """Skill Runtime mission Phase 7: each incident row in the frozen
    snapshot records exactly which AnalysisRun/SkillSnapshot/output
    produced its analysis — not just the analysis content itself — so a
    report can always be traced back to the exact skill version and raw
    output that fed it."""
    import uuid as _uuid

    from app.api.v1.routers import analysis as analysis_router
    from app.models.models import AnalysisRun, Job
    from app.skills.registry import resolve_active_snapshot

    make_user(db_session, "operator2", "NOC")
    headers = auth_headers(client, "operator2")
    shift = _create_active_shift(db_session)
    incident_id = _create_incident(client, headers)

    active_snapshot = resolve_active_snapshot(db_session, analysis_router.SKILL_NAME)
    job = Job(
        job_type="log_triage",
        status="COMPLETED",
        incident_id=_uuid.UUID(incident_id),
        skill_name=analysis_router.SKILL_NAME,
        skill_version=analysis_router.SKILL_VERSION,
        skill_hash=active_snapshot.content_hash,
        skill_snapshot_id=active_snapshot.id,
        correlation_id=str(_uuid.uuid4()),
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    run = AnalysisRun(
        incident_id=_uuid.UUID(incident_id),
        job_id=job.id,
        result_json={"summary_en": "x"},
        skill_name=analysis_router.SKILL_NAME,
        skill_version=analysis_router.SKILL_VERSION,
        skill_hash=active_snapshot.content_hash,
        skill_snapshot_id=active_snapshot.id,
        output_sha256="a" * 64,
        current=True,
    )
    db_session.add(run)
    db_session.commit()

    resp = client.post(
        f"/api/v1/shifts/{shift.id}/reports",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    from app.models.models import ReportSnapshot

    report_id = resp.json()["id"]
    from app.models.models import Report

    report = db_session.get(Report, _uuid.UUID(report_id))
    snapshot = db_session.get(ReportSnapshot, report.snapshot_id)
    row = next(i for i in snapshot.snapshot_json["incidents"] if i["id"] == incident_id)
    assert row["analysis_run_id"] == str(run.id)
    assert row["analysis_skill_snapshot_id"] == str(active_snapshot.id)
    assert row["analysis_skill_hash"] == active_snapshot.content_hash
    assert row["analysis_output_sha256"] == "a" * 64


def test_report_snapshot_freezes_evidence_byte_identity(client, db_session):
    make_user(db_session, "operator-snapshot", "NOC")
    headers = auth_headers(client, "operator-snapshot")
    shift = _create_active_shift(db_session)
    incident_id = _create_incident(client, headers)
    evidence = Evidence(
        incident_id=incident_id,
        evidence_type="ALERT_SCREENSHOT",
        bucket="noc-evidence",
        object_key="incidents/frozen/alert.png",
        original_filename="alert.png",
        mime_type="image/png",
        byte_size=7,
        sha256="a" * 64,
        version_id="version-7",
    )
    db_session.add(evidence)
    db_session.commit()
    from app.api.v1.routers.reports import _build_snapshot
    from app.skills.registry import resolve_active_snapshot

    snapshot = _build_snapshot(db_session, shift, resolve_active_snapshot(db_session, "daily-alert-report"))
    frozen = snapshot["incidents"][0]["screenshots"][0]
    assert frozen == {
        "evidence_id": str(evidence.id),
        "evidence_type": "ALERT_SCREENSHOT",
        "bucket": "noc-evidence",
        "object_key": "incidents/frozen/alert.png",
        "version_id": "version-7",
        "sha256": "a" * 64,
        "filename": "alert.png",
        "content_type": "image/png",
        "byte_size": 7,
    }


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
    assert out["report_version_id"]

    # A later write at the same key must not change the immutable artifact
    # identified by the Report row.
    minio.put_object(
        Bucket=settings.minio_bucket_reports,
        Key=f"reports/{job_id}/report.docx",
        Body=b"replacement report bytes",
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    download = client.get(f"/api/v1/reports/{report_id}/download-url", headers=headers)
    assert download.status_code == 200
    assert "download_url" in download.json()
    assert client.get(f"/api/v1/reports/{report_id}/download", headers=headers).content == b"fake docx bytes for test"


def test_document_preview_and_screenshot_proxy_serve_bridge_written_artifacts(client, db_session):
    """Simulates what the bridge writes for the report-document-v1 renderer
    profile: report.docx, document.json (browser-safe preview) and
    document.screenshots.json (private bucket/object_key index). The API
    must serve the preview JSON and proxy screenshot bytes by index,
    without ever handing the browser a bucket/object_key directly."""
    make_user(db_session, "operator3", "NOC")
    headers = auth_headers(client, "operator3")
    shift = _create_active_shift(db_session)

    resp = client.post(
        f"/api/v1/shifts/{shift.id}/reports",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp.status_code == 201
    report_id = resp.json()["id"]
    job_id = resp.json()["job_id"]

    not_ready = client.get(f"/api/v1/reports/{report_id}/document", headers=headers)
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
    preview_payload = {
        "title": "Daily Alert & Log Analysis Report",
        "metadata": [],
        "blocks": [
            {"type": "heading", "text": "Alerts", "level": 1},
            {
                "type": "incident_evidence",
                "heading": "Alert #1 - Payment timeout",
                "incident_id": "inc-001",
                "metadata": [],
                "links": [],
                "log_file": None,
                "screenshots": [{"type": "screenshot", "index": 0, "filename": "alert.png"}],
            },
        ],
    }
    document_bytes = json.dumps(preview_payload).encode("utf-8")
    document_put = minio.put_object(
        Bucket=settings.minio_bucket_reports,
        Key=f"reports/{job_id}/document.json",
        Body=document_bytes,
        ContentType="application/json",
    )
    screenshot_put = minio.put_object(
        Bucket="noc-evidence",
        Key="inc-001.png",
        Body=b"fake png bytes",
        ContentType="image/png",
    )
    screenshot_index_bytes = json.dumps([{
        "bucket": "noc-evidence", "object_key": "inc-001.png", "filename": "alert.png",
        "version_id": screenshot_put["VersionId"],
    }]).encode("utf-8")
    screenshot_index_put = minio.put_object(
        Bucket=settings.minio_bucket_reports,
        Key=f"reports/{job_id}/document.screenshots.json",
        Body=screenshot_index_bytes,
        ContentType="application/json",
    )

    # Simulate the bridge's durable artifact metadata handoff. The API must
    # use these exact versions, not a later HEAD at the deterministic key.
    from app.models.models import Report
    report = db_session.get(Report, __import__("uuid").UUID(report_id))
    report.document_object_key = f"reports/{job_id}/document.json"
    report.document_version_id = document_put["VersionId"]
    report.document_sha256 = sha256_of_bytes(document_bytes)
    report.document_byte_size = len(document_bytes)
    report.document_content_type = "application/json"
    report.screenshots_object_key = f"reports/{job_id}/document.screenshots.json"
    report.screenshots_version_id = screenshot_index_put["VersionId"]
    report.screenshots_sha256 = sha256_of_bytes(screenshot_index_bytes)
    report.screenshots_byte_size = len(screenshot_index_bytes)
    report.screenshots_content_type = "application/json"
    db_session.commit()

    doc_resp = client.get(f"/api/v1/reports/{report_id}/document", headers=headers)
    assert doc_resp.status_code == 200
    body = doc_resp.json()
    assert body["blocks"][0]["text"] == "Alerts"
    dumped = json.dumps(body)
    assert "noc-evidence" not in dumped
    assert "inc-001.png" not in dumped

    shot_resp = client.get(f"/api/v1/reports/{report_id}/document/screenshots/0", headers=headers)
    assert shot_resp.status_code == 200
    assert shot_resp.content == b"fake png bytes"
    assert shot_resp.headers["content-type"] == "image/png"

    missing_shot = client.get(f"/api/v1/reports/{report_id}/document/screenshots/9", headers=headers)
    assert missing_shot.status_code == 404

    # A later version at the same preview key cannot alter this historical
    # report's structured document.
    minio.put_object(
        Bucket=settings.minio_bucket_reports,
        Key=f"reports/{job_id}/document.json",
        Body=b'{"title":"replacement"}',
        ContentType="application/json",
    )
    historical = client.get(f"/api/v1/reports/{report_id}/document", headers=headers)
    assert historical.status_code == 200
    assert historical.json()["blocks"][0]["text"] == "Alerts"

    # Replacing the deterministic screenshot-manifest key must not change
    # which evidence version the historical ReportDocument serves.
    minio.put_object(
        Bucket=settings.minio_bucket_reports,
        Key=f"reports/{job_id}/document.screenshots.json",
        Body=b"[]",
        ContentType="application/json",
    )
    historical_shot = client.get(
        f"/api/v1/reports/{report_id}/document/screenshots/0",
        headers=headers,
    )
    assert historical_shot.status_code == 200
    assert historical_shot.content == b"fake png bytes"
