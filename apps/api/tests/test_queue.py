"""
Milestone 11 (RabbitMQ) — tests run against the real RabbitMQ container
(localhost:55672), following the same "test against real infrastructure"
pattern used in test_evidence.py (real MinIO) and test_ocr.py (real
PaddleOCR): no mocked broker.
"""

import json
import uuid

import jsonschema
import pytest

from app.core.queue import (
    JOB_TYPES,
    _queue_names,
    build_job_message,
    declare_topology,
    get_connection,
    publish_job,
    publish_status_event,
    queue_message_count,
)
from app.jobs import enqueue_job, open_channel
from app.outbox import dispatch_pending_events
from app.models.models import Job

from tests.conftest import auth_headers, make_user


def _purge_all(channel):
    for job_type in JOB_TYPES:
        names = _queue_names(job_type)
        channel.queue_purge(names["main"])
        channel.queue_purge(names["retry"])
        channel.queue_purge(names["dlq"])
    channel.queue_purge("noc.events.log")


def test_declare_topology_is_idempotent():
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        declare_topology(channel)  # second call must not raise
        _purge_all(channel)
    finally:
        connection.close()


def test_publish_job_lands_on_the_correct_queue():
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        _purge_all(channel)

        job_id = uuid.uuid4()
        publish_job(
            channel,
            job_id=job_id,
            job_type="log_triage",
            incident_id="INC-1001",
            object_refs=[{"bucket": "noc-evidence", "key": "incidents/x/logs/a.log"}],
            model="claude-sonnet-5",
            effort="medium",
            skill_name="log-triage",
            skill_version="1",
            correlation_id="corr-1",
        )

        names = _queue_names("log_triage")
        method, properties, body = channel.basic_get(names["main"], auto_ack=True)
        assert method is not None
        payload = json.loads(body)
        assert payload["job_id"] == str(job_id)
        assert payload["job_type"] == "log_triage"
        assert payload["incident_id"] == "INC-1001"
        assert payload["object_refs"][0]["key"] == "incidents/x/logs/a.log"

        _purge_all(channel)
    finally:
        connection.close()


def test_publish_status_event_lands_on_events_log():
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        _purge_all(channel)

        job_id = uuid.uuid4()
        publish_status_event(channel, job_id=job_id, event="started", detail={"attempt": 1})

        method, properties, body = channel.basic_get("noc.events.log", auto_ack=True)
        assert method is not None
        payload = json.loads(body)
        assert payload["job_id"] == str(job_id)
        assert payload["event"] == "started"

        _purge_all(channel)
    finally:
        connection.close()


def test_enqueue_job_creates_row_and_publishes(db_session):
    make_user(db_session, "operator1", "NOC")

    with open_channel() as channel:
        _purge_all(channel)
        job = enqueue_job(
            db_session,
            job_type="daily_report",
            requested_by=None,
            incident_id=None,
            object_refs=[],
            model="claude-sonnet-5",
            effort="high",
            skill_name="daily-report",
            skill_version="1",
        )
        db_session.commit()
        assert job.id is not None
        assert job.status == "QUEUED"

        # enqueue_job only writes the outbox row now (Reliability mission
        # Batch A) — dispatch it explicitly rather than expecting a
        # synchronous publish.
        dispatch_pending_events(db_session, channel)

        names = _queue_names("daily_report")
        method, properties, body = channel.basic_get(names["main"], auto_ack=True)
        assert method is not None
        payload = json.loads(body)
        assert payload["job_id"] == str(job.id)

        _purge_all(channel)

    fetched = db_session.get(Job, job.id)
    assert fetched is not None
    assert fetched.job_type == "daily_report"


def test_admin_jobs_and_dlq_endpoints(client, db_session):
    make_user(db_session, "admin1", "Admin")
    headers = auth_headers(client, "admin1")

    with open_channel() as channel:
        _purge_all(channel)
        enqueue_job(
            db_session,
            job_type="log_triage",
            requested_by=None,
            incident_id=None,
            object_refs=[],
            model="claude-sonnet-5",
            effort="medium",
            skill_name="log-triage",
            skill_version="1",
        )
        db_session.commit()
        dispatch_pending_events(db_session, channel)
        _purge_all(channel)

    jobs_resp = client.get("/api/v1/admin/jobs", headers=headers)
    assert jobs_resp.status_code == 200
    jobs = jobs_resp.json()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "log_triage"
    assert jobs[0]["status"] == "QUEUED"

    dlq_resp = client.get("/api/v1/admin/dlq", headers=headers)
    assert dlq_resp.status_code == 200
    dlq = dlq_resp.json()
    assert {row["queue"] for row in dlq} == {
        _queue_names(jt)["dlq"] for jt in JOB_TYPES
    }
    assert all(row["message_count"] == 0 for row in dlq)


def test_admin_jobs_requires_permission(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.get("/api/v1/admin/jobs", headers=headers)
    assert resp.status_code == 403


def test_dlq_requeue_and_purge(client, db_session):
    """Milestone 17 (DLQ controls)."""
    from app.core.queue import send_to_dlq
    from app.models.models import Job

    make_user(db_session, "admin1", "Admin")
    headers = auth_headers(client, "admin1")
    make_user(db_session, "operator1", "NOC")
    noc_headers = auth_headers(client, "operator1")

    with open_channel() as channel:
        _purge_all(channel)
        job = enqueue_job(
            db_session,
            job_type="log_triage",
            requested_by=None,
            incident_id=None,
            object_refs=[],
            model="claude-sonnet-5",
            effort="medium",
            skill_name="log-triage",
            skill_version="1",
        )
        db_session.commit()
        dispatch_pending_events(db_session, channel)
        job_id = job.id
        # Simulate the bridge dead-lettering this job after exhausting
        # retries (mirrors bridge/noc_bridge/service.py's _handle_delivery).
        channel.queue_purge(_queue_names("log_triage")["main"])
        send_to_dlq(
            channel,
            job_type="log_triage",
            body={"job_id": str(job_id), "job_type": "log_triage", "attempt": 4},
        )
        job.status = "FAILED"
        job.error_code = "RuntimeError"
        job.error_message = "boom"
        db_session.commit()

    # Non-Admin can't touch DLQ controls.
    forbidden = client.post("/api/v1/admin/dlq/log_triage/requeue", headers=noc_headers)
    assert forbidden.status_code == 403

    resp = client.post("/api/v1/admin/dlq/log_triage/requeue", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"job_type": "log_triage", "requeued": 1, "purged": 0}

    db_session.expire_all()
    reloaded = db_session.get(Job, job_id)
    assert reloaded.status == "QUEUED"
    assert reloaded.attempt == 1
    assert reloaded.error_code is None

    with open_channel() as channel:
        assert queue_message_count(channel, _queue_names("log_triage")["main"]) == 1
        assert queue_message_count(channel, _queue_names("log_triage")["dlq"]) == 0
        _purge_all(channel)
        send_to_dlq(
            channel,
            job_type="log_triage",
            body={"job_id": str(job_id), "job_type": "log_triage", "attempt": 4},
        )

    # Requeuing an empty DLQ is a no-op, not an error.
    empty_resp = client.post("/api/v1/admin/dlq/daily_report/requeue", headers=headers)
    assert empty_resp.json() == {"job_type": "daily_report", "requeued": 0, "purged": 0}

    purge_resp = client.post("/api/v1/admin/dlq/log_triage/purge", headers=headers)
    assert purge_resp.status_code == 200
    assert purge_resp.json() == {"job_type": "log_triage", "requeued": 0, "purged": 1}

    with open_channel() as channel:
        assert queue_message_count(channel, _queue_names("log_triage")["dlq"]) == 0
        _purge_all(channel)

    unknown_resp = client.post("/api/v1/admin/dlq/not_a_real_type/requeue", headers=headers)
    assert unknown_resp.status_code == 404


# -- Skill Runtime mission Phase 14: canonical job message protocol --------


def test_build_job_message_produces_a_schema_valid_message():
    message = build_job_message(
        job_id=uuid.uuid4(),
        job_type="log_triage",
        incident_id="INC-1001",
        object_refs=[{"bucket": "noc-evidence", "key": "a.log"}],
        model="claude-sonnet-5",
        effort="medium",
        skill_name="log-triage-summary",
        skill_version="1",
        correlation_id="corr-1",
    )
    # build_job_message itself validates before returning (Phase 14) --
    # this just proves the resulting shape also matches the same schema
    # the bridge validates incoming deliveries against, i.e. the two sides
    # genuinely share one contract rather than two hand-kept-in-sync ones.
    from app.core.queue import _job_message_schema

    jsonschema.validate(message, _job_message_schema)


def test_build_job_message_rejects_a_message_missing_a_required_field(monkeypatch):
    """A producer-side bug that drifts from the protocol (here: simulated
    by monkeypatching the schema to require a field build_job_message never
    sets) must fail loudly at build time, not silently publish a malformed
    message for the bridge to choke on later."""
    import app.core.queue as queue_module

    broken_schema = {**queue_module._job_message_schema, "required": ["job_id", "not_a_real_field"]}
    monkeypatch.setattr(queue_module, "_job_message_schema", broken_schema)

    with pytest.raises(jsonschema.ValidationError):
        build_job_message(
            job_id=uuid.uuid4(),
            job_type="log_triage",
            incident_id=None,
            object_refs=[],
            model="claude-sonnet-5",
            effort="medium",
            skill_name="log-triage-summary",
            skill_version="1",
            correlation_id="corr-1",
        )
