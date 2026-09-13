"""Milestone 13 (Real Log Triage) — tests run against real Postgres,
RabbitMQ, and MinIO (same pattern as test_evidence.py/test_queue.py): no
mocked analyzer, no mocked broker/storage. The bridge/sandbox themselves
aren't started here (that's bridge/tests/test_bridge.py's job); this
suite verifies the API's own slice: it enqueues a real job and, once a
result artifact exists in noc-job-artifacts (simulating what the bridge
would have written), syncs it into the AnalysisRun row on poll.
"""
import hashlib
import json
import time
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
            "sha256": hashlib.sha256(log_bytes).hexdigest(),
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

    # Skill Runtime mission Phase 6: the created AnalysisRun carries a
    # direct FK to the exact SkillSnapshot row used, not just its hash.
    from app.api.v1.routers import analysis as analysis_router
    from app.skills.registry import resolve_active_snapshot

    active_snapshot = resolve_active_snapshot(db_session, analysis_router.SKILL_NAME)
    assert out["skill_snapshot_id"] == str(active_snapshot.id)

    # No fallback analyzer in the API — the real message actually landed
    # on the real queue for a real bridge to pick up. Publishing is now
    # deferred to the outbox dispatcher background thread (Reliability
    # mission Batch A), which polls roughly once a second, so poll for the
    # message instead of assuming it's there the instant the request
    # returns.
    names = _queue_names("log_triage")
    method = properties = body = None
    for _ in range(50):
        method, properties, body = channel.basic_get(names["main"], auto_ack=True)
        if method is not None:
            break
        time.sleep(0.2)
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


def test_poll_syncs_telemetry_when_available(client, db_session):
    """AI cost-optimization mission Phase 1: telemetry.json uploaded
    alongside result.json is merged into the AnalysisRun row and surfaced
    on AnalysisRunOut. A job with no telemetry.json (older run / upload
    failure) must still succeed, with all telemetry fields null/False —
    the best-effort contract must never block a normal poll."""
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    # --- Job with telemetry ---
    incident_id = _create_incident(client, headers)
    _upload_log_evidence(client, headers, incident_id)
    resp = client.post(
        f"/api/v1/incidents/{incident_id}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "low"},
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
        "confidence": 0.9,
    }
    telemetry = {
        "model": "claude-sonnet-5",
        "effort": "medium",
        "cost_usd": 0.0123,
        "duration_ms": 4567,
        "num_turns": 3,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_creation_tokens": 500,
        "cache_read_tokens": 100,
        "raw_input_bytes": 90000,
        "evidence_bytes": 12000,
        "preprocessing_ratio": 0.8667,
        "escalated": True,
        "escalation_reason": "low confidence",
        "confidence": 0.9,
    }
    minio = get_client()
    minio.put_object(
        Bucket=settings.minio_bucket_job_artifacts,
        Key=f"jobs/{job_id}/result.json",
        Body=json.dumps(result).encode("utf-8"),
        ContentType="application/json",
    )
    minio.put_object(
        Bucket=settings.minio_bucket_job_artifacts,
        Key=f"jobs/{job_id}/telemetry.json",
        Body=json.dumps(telemetry).encode("utf-8"),
        ContentType="application/json",
    )

    polled = client.get(f"/api/v1/incidents/{incident_id}/analysis-runs/{job_id}", headers=headers)
    assert polled.status_code == 200
    out = polled.json()
    assert out["result"] == result
    assert out["input_tokens"] == 1200
    assert out["output_tokens"] == 300
    assert out["cache_creation_tokens"] == 500
    assert out["cache_read_tokens"] == 100
    assert out["estimated_cost_usd"] == 0.0123
    assert out["duration_ms"] == 4567
    assert out["num_turns"] == 3
    assert out["confidence"] == 0.9
    assert out["escalated"] is True
    assert out["escalation_reason"] == "low confidence"
    assert out["raw_input_bytes"] == 90000
    assert out["evidence_bytes"] == 12000
    assert out["preprocessing_ratio"] == 0.8667

    # --- Job with no telemetry.json at all: still succeeds, all null/False ---
    incident_id2 = _create_incident(client, headers)
    _upload_log_evidence(client, headers, incident_id2)
    resp2 = client.post(
        f"/api/v1/incidents/{incident_id2}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "low"},
        headers=headers,
    )
    assert resp2.status_code == 201
    job_id2 = resp2.json()["job_id"]
    job2 = db_session.get(Job, __import__("uuid").UUID(job_id2))
    job2.status = "COMPLETED"
    job2.completed_at = datetime.now(timezone.utc)
    db_session.commit()
    minio.put_object(
        Bucket=settings.minio_bucket_job_artifacts,
        Key=f"jobs/{job_id2}/result.json",
        Body=json.dumps(result).encode("utf-8"),
        ContentType="application/json",
    )

    polled2 = client.get(f"/api/v1/incidents/{incident_id2}/analysis-runs/{job_id2}", headers=headers)
    assert polled2.status_code == 200
    out2 = polled2.json()
    assert out2["result"] == result
    assert out2["input_tokens"] is None
    assert out2["estimated_cost_usd"] is None
    assert out2["escalated"] is False
    assert out2["escalation_reason"] is None
    assert out2["preprocessing_ratio"] is None


def test_request_analysis_exact_cache_hit_skips_queue(client, db_session):
    """AI cost-optimization mission Phase 6: a second incident whose log
    evidence has byte-identical content (same sha256) as an already-
    completed analysis reuses that result immediately — no RabbitMQ
    message, no bridge/Claude invocation, Job created already COMPLETED
    with used_cache/cache_type set."""
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    # First incident: request + simulate the bridge completing it.
    incident_a = _create_incident(client, headers)
    _upload_log_evidence(client, headers, incident_a)
    resp_a = client.post(
        f"/api/v1/incidents/{incident_a}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp_a.status_code == 201
    job_a_id = resp_a.json()["job_id"]

    from app.models.models import Job

    job_a = db_session.get(Job, __import__("uuid").UUID(job_a_id))
    job_a.status = "COMPLETED"
    job_a.completed_at = datetime.now(timezone.utc)
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
        Key=f"jobs/{job_a_id}/result.json",
        Body=json.dumps(result).encode("utf-8"),
        ContentType="application/json",
    )
    # Sync it into the AnalysisRun row (what a real poll would do).
    polled = client.get(f"/api/v1/incidents/{incident_a}/analysis-runs/{job_a_id}", headers=headers)
    assert polled.json()["result"] == result

    # Second incident, byte-identical log content (_upload_log_evidence
    # always writes the same fixed bytes) -> same evidence sha256.
    connection = get_connection()
    channel = connection.channel()
    declare_topology(channel)
    # job_a's own enqueue publishes asynchronously via the background outbox
    # dispatcher (two-phase enqueue-then-dispatch, Batch A) — without
    # explicitly dispatching+draining it here first, that publish can race
    # _purge_all below and land in the queue afterward, making the "no
    # message was published" assertion further down flaky.
    from app.outbox import dispatch_pending_events

    dispatch_pending_events(db_session, channel)
    _purge_all(channel)

    incident_b = _create_incident(client, headers)
    _upload_log_evidence(client, headers, incident_b)
    resp_b = client.post(
        f"/api/v1/incidents/{incident_b}/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
        headers=headers,
    )
    assert resp_b.status_code == 201, resp_b.text
    out_b = resp_b.json()
    assert out_b["status"] == "COMPLETED"
    assert out_b["result"] == result
    assert out_b["used_cache"] is True
    assert out_b["cache_type"] == "exact"

    # No message was published for the cache-hit job.
    names = _queue_names("log_triage")
    method, _properties, _body = channel.basic_get(names["main"], auto_ack=True)
    assert method is None
    connection.close()


def _current_skill_hash(db_session) -> str:
    from app.api.v1.routers import analysis as analysis_router
    from app.skills.registry import get_or_create_snapshot

    snapshot = get_or_create_snapshot(db_session, analysis_router.SKILL_NAME)
    db_session.commit()
    return snapshot.content_hash


def _make_completed_run(db_session, incident_id, evidence_id, sha256, **overrides):
    """AI cost-optimization mission Phase 2, Issue 5 test helper: builds a
    completed AnalysisRun row directly (bypassing the HTTP/job flow) so
    cache-lookup version/override behavior can be tested in isolation."""
    import uuid as _uuid
    from datetime import datetime, timezone as _tz

    from app.api.v1.routers import analysis as analysis_router
    from app.models.models import AnalysisRun, Job

    job = Job(
        job_type="log_triage",
        status="COMPLETED",
        incident_id=_uuid.UUID(incident_id),
        model=overrides.get("model", "claude-sonnet-5"),
        effort=overrides.get("effort", "low"),
        skill_name=analysis_router.SKILL_NAME,
        skill_version=analysis_router.SKILL_VERSION,
        correlation_id=str(_uuid.uuid4()),
        completed_at=datetime.now(_tz.utc),
    )
    db_session.add(job)
    db_session.flush()
    run = AnalysisRun(
        incident_id=_uuid.UUID(incident_id),
        job_id=job.id,
        log_evidence_id=_uuid.UUID(evidence_id),
        result_json={"summary": "cached"},
        model=overrides.get("model", "claude-sonnet-5"),
        effort=overrides.get("effort", "low"),
        skill_name=analysis_router.SKILL_NAME,
        skill_version=overrides.get("skill_version", analysis_router.SKILL_VERSION),
        skill_hash=overrides.get("skill_hash", _current_skill_hash(db_session)),
        cache_contract_version=overrides.get(
            "cache_contract_version", analysis_router.CACHE_CONTRACT_VERSION
        ),
        input_manifest_sha256=sha256,
        current=True,
    )
    db_session.add(run)
    db_session.commit()
    return run


def test_cached_analysis_run_lookup_matches_same_contract_version(client, db_session):
    """AI cost-optimization mission Phase 2, Issue 5: a cached run produced
    under the current cache contract version (schema/preprocessor/policy
    all unchanged) and skill version is reused."""
    make_user(db_session, "operator2", "NOC")
    headers = auth_headers(client, "operator2")
    incident = _create_incident(client, headers)
    evidence = _upload_log_evidence(client, headers, incident)

    from app.api.v1.routers.analysis import _find_cached_analysis_run
    from app.models.models import Evidence
    import uuid as _uuid

    log_evidence = db_session.get(Evidence, _uuid.UUID(evidence["id"]))
    _make_completed_run(db_session, incident, evidence["id"], log_evidence.sha256)

    hit = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort=None,
        skill_hash=_current_skill_hash(db_session),
    )
    assert hit is not None


def test_cached_analysis_run_lookup_invalidated_by_cache_contract_version_mismatch(client, db_session):
    """A cached run produced under an older/different cache contract
    version (schema, preprocessor, or AI policy changed) must not be
    reused — the mission's Issue 5 requirement that any of those three
    axes changing invalidates stale cache entries."""
    make_user(db_session, "operator3", "NOC")
    headers = auth_headers(client, "operator3")
    incident = _create_incident(client, headers)
    evidence = _upload_log_evidence(client, headers, incident)

    from app.api.v1.routers.analysis import _find_cached_analysis_run
    from app.models.models import Evidence
    import uuid as _uuid

    log_evidence = db_session.get(Evidence, _uuid.UUID(evidence["id"]))
    _make_completed_run(
        db_session, incident, evidence["id"], log_evidence.sha256,
        cache_contract_version="0.0.0",
    )

    hit = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort=None,
        skill_hash=_current_skill_hash(db_session),
    )
    assert hit is None


def test_cached_analysis_run_lookup_invalidated_by_skill_version_mismatch(client, db_session):
    make_user(db_session, "operator4", "NOC")
    headers = auth_headers(client, "operator4")
    incident = _create_incident(client, headers)
    evidence = _upload_log_evidence(client, headers, incident)

    from app.api.v1.routers.analysis import _find_cached_analysis_run
    from app.models.models import Evidence
    import uuid as _uuid

    log_evidence = db_session.get(Evidence, _uuid.UUID(evidence["id"]))
    _make_completed_run(
        db_session, incident, evidence["id"], log_evidence.sha256,
        skill_version="0",
    )

    hit = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort=None,
        skill_hash=_current_skill_hash(db_session),
    )
    assert hit is None


def test_cached_analysis_run_lookup_invalidated_by_skill_hash_mismatch(client, db_session):
    """Skill Registry (Reliability mission Batch B): a cached run produced
    under *different skill content* (a different content hash) is not
    reusable, even under the same skill_name/skill_version/cache_contract_
    version labels — this is the drift the hash exists to catch."""
    make_user(db_session, "operator4b", "NOC")
    headers = auth_headers(client, "operator4b")
    incident = _create_incident(client, headers)
    evidence = _upload_log_evidence(client, headers, incident)

    from app.api.v1.routers.analysis import _find_cached_analysis_run
    from app.models.models import Evidence
    import uuid as _uuid

    log_evidence = db_session.get(Evidence, _uuid.UUID(evidence["id"]))
    _make_completed_run(
        db_session, incident, evidence["id"], log_evidence.sha256,
        skill_hash="0" * 64,
    )

    hit = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort=None,
        skill_hash=_current_skill_hash(db_session),
    )
    assert hit is None


def test_cached_analysis_run_lookup_respects_explicit_higher_effort_override(client, db_session):
    """AI cost-optimization mission Phase 2, Issue 5: an explicit operator
    request for a different effort tier than the cached run must NOT
    silently reuse that (incompatible) cached result."""
    make_user(db_session, "operator5", "NOC")
    headers = auth_headers(client, "operator5")
    incident = _create_incident(client, headers)
    evidence = _upload_log_evidence(client, headers, incident)

    from app.api.v1.routers.analysis import _find_cached_analysis_run
    from app.models.models import Evidence
    import uuid as _uuid

    log_evidence = db_session.get(Evidence, _uuid.UUID(evidence["id"]))
    _make_completed_run(
        db_session, incident, evidence["id"], log_evidence.sha256,
        effort="low",
    )

    # Explicit request for "medium" must not match the cached "low" run.
    skill_hash = _current_skill_hash(db_session)
    hit = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort="medium",
        skill_hash=skill_hash,
    )
    assert hit is None

    # An unset (None) effort request still matches (the common case).
    hit_default = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort=None,
        skill_hash=skill_hash,
    )
    assert hit_default is not None

    # Explicit request for the SAME effort as the cached run still matches.
    hit_same = _find_cached_analysis_run(
        db_session, log_evidence=log_evidence, requested_model=None, requested_effort="low",
        skill_hash=skill_hash,
    )
    assert hit_same is not None


def test_analysis_requires_permission(client, db_session):
    resp = client.post(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000000/analysis-runs",
        json={"model": "claude-sonnet-5", "effort": "medium"},
    )
    assert resp.status_code == 401
