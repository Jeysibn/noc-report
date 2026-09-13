"""Milestone 12 tests — run against the real RabbitMQ, MinIO, Docker, and
Postgres (the same four containers/services apps/api's own suite uses),
matching the project's "test against real infrastructure" stance. Assumes
`docker compose -f infrastructure/docker-compose.dev.yml up -d` and
`alembic upgrade head` have already been run against apps/api (so the
`jobs` table exists).
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import uuid

import pytest

# `test_end_to_end_log_triage_job` now invokes the real Claude Code CLI
# (ADR 0003), authenticated via the operator's own OAuth credential, and
# consumes real usage on their subscription. Routine `pytest` runs must
# not do that silently — set NOC_BRIDGE_LIVE_CLAUDE_TESTS=1 to opt in,
# and run it yourself (this can't be exercised by Claude Code itself: its
# own auto-mode classifier blocks a nested `claude` CLI invocation via
# the Bash tool).
_LIVE_CLAUDE_TESTS = bool(os.environ.get("NOC_BRIDGE_LIVE_CLAUDE_TESTS"))

from noc_bridge import db
from noc_bridge.config import BridgeSettings
from noc_bridge.queue_topology import JOB_TYPES, declare_topology, get_connection, queue_names
from noc_bridge.sandbox_runner import ensure_image_built
from noc_bridge.service import BridgeService
from noc_bridge.storage import ChecksumMismatch, download_object, get_client, sha256_of_file, upload_artifact
from noc_bridge.validation import OutputValidationError, validate_output

SETTINGS = BridgeSettings()


@pytest.fixture
def pg_conn():
    conn = db.get_connection(SETTINGS.database_url)
    yield conn
    conn.close()


@pytest.fixture
def minio_client():
    return get_client(SETTINGS)


@pytest.fixture
def mq_channel():
    connection = get_connection(SETTINGS.rabbitmq_url)
    channel = connection.channel()
    declare_topology(channel)
    for job_type in JOB_TYPES:
        names = queue_names(job_type)
        channel.queue_purge(names["main"])
        channel.queue_purge(names["retry"])
        channel.queue_purge(names["dlq"])
    channel.queue_purge("noc.events.log")
    yield channel
    connection.close()


def _insert_job_row(pg_conn, job_id: uuid.UUID, job_type: str) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (id, job_type, status, attempt, correlation_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(job_id), job_type, "QUEUED", 1, str(uuid.uuid4())),
        )
    pg_conn.commit()


def _delete_job_row(pg_conn, job_id: uuid.UUID) -> None:
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE id = %s", (str(job_id),))
    pg_conn.commit()


# -- storage: real MinIO round-trip ---------------------------------------


def test_download_verifies_checksum(minio_client):
    bucket = SETTINGS.minio_bucket_evidence
    key = f"jobs-test/{uuid.uuid4()}/log.txt"
    content = b"2026-09-09T00:00:00Z ERROR something broke\n"

    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "log.txt"
        src.write_bytes(content)
        good_sha = sha256_of_file(src)
        minio_client.upload_file(str(src), bucket, key)

        dest = pathlib.Path(tmp) / "downloaded.txt"
        download_object(minio_client, bucket=bucket, object_key=key, dest_path=dest, expected_sha256=good_sha)
        assert dest.read_bytes() == content

        with pytest.raises(ChecksumMismatch):
            download_object(
                minio_client,
                bucket=bucket,
                object_key=key,
                dest_path=dest,
                expected_sha256="0" * 64,
            )


def test_upload_artifact_roundtrip(minio_client):
    bucket = SETTINGS.minio_bucket_job_artifacts
    key = f"jobs-test/{uuid.uuid4()}/result.json"
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "result.json"
        src.write_text(json.dumps({"ok": True}))
        digest = upload_artifact(minio_client, bucket=bucket, object_key=key, src_path=src)
        assert len(digest) == 64

        fetched = pathlib.Path(tmp) / "fetched.json"
        minio_client.download_file(bucket, key, str(fetched))
        assert json.loads(fetched.read_text()) == {"ok": True}


# -- validation: pure unit tests -------------------------------------------


def test_validate_log_triage_accepts_well_formed_output():
    validate_output(
        "log_triage",
        {
            "summary_en": "one error found",
            "summary_zh": "发现一个错误",
            "key_finds": [
                {
                    "label_en": "NullPointerException", "label_zh": "空指针异常",
                    "detail_en": "null pointer", "detail_zh": "空指针",
                    "count": 1, "percentage": 100.0,
                }
            ],
            "secondary_finds": [],
            "likely_cause_en": "null pointer", "likely_cause_zh": "空指针",
            "recommended_action_en": "escalate", "recommended_action_zh": "升级",
            "severity_signal": "high",
            "confidence": 0.9,
        },
    )


def test_validate_log_triage_rejects_bad_severity():
    with pytest.raises(OutputValidationError):
        validate_output(
            "log_triage",
            {
                "summary": "x",
                "likely_cause": "x",
                "severity_signal": "extremely-bad",
                "recommended_action": "x",
                "confidence": 0.5,
            },
        )


def test_validate_output_rejects_none():
    with pytest.raises(OutputValidationError):
        validate_output("log_triage", None)


def test_validate_unsupported_job_type():
    with pytest.raises(OutputValidationError):
        validate_output("something_unknown", {"a": 1})


# -- full pipeline: real RabbitMQ + Postgres + MinIO + Docker --------------


@pytest.mark.skipif(
    not _LIVE_CLAUDE_TESTS,
    reason="invokes the real Claude Code CLI and spends real subscription "
    "usage — set NOC_BRIDGE_LIVE_CLAUDE_TESTS=1 to opt in and run this "
    "yourself (e.g. `NOC_BRIDGE_LIVE_CLAUDE_TESTS=1 python3 -m pytest "
    "tests/test_bridge.py::test_end_to_end_log_triage_job`)",
)
def test_end_to_end_log_triage_job(pg_conn, minio_client, mq_channel):
    job_id = uuid.uuid4()
    _insert_job_row(pg_conn, job_id, "log_triage")

    bucket = SETTINGS.minio_bucket_evidence
    key = f"jobs-test/{job_id}/log.txt"
    log_bytes = b"2026-09-09T00:00:00Z ERROR unhandled exception in request path\n"

    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "log.txt"
        src.write_bytes(log_bytes)
        sha = sha256_of_file(src)
        minio_client.upload_file(str(src), bucket, key)

    payload = {
        "job_id": str(job_id),
        "job_type": "log_triage",
        "incident_id": "INC-TEST",
        "object_refs": [{"bucket": bucket, "key": key, "sha256": sha}],
        "model": "claude-sonnet-5",
        "effort": "medium",
        "skill_name": "log-triage-summary",
        "skill_version": "1",
        "correlation_id": str(uuid.uuid4()),
        "attempt": 1,
    }

    service = BridgeService(SETTINGS)

    ensure_image_built(__import__("docker").from_env())

    # Publish onto the real main queue and pull it back with basic_get so
    # _handle_delivery gets a real delivery_tag to ack (rather than faking
    # one, which the broker rejects).
    names = queue_names("log_triage")
    mq_channel.confirm_delivery()
    mq_channel.basic_publish(
        exchange="noc.jobs",
        routing_key=names["routing_key"],
        body=json.dumps(payload).encode("utf-8"),
    )
    method, properties, body = mq_channel.basic_get(names["main"])
    assert method is not None

    service._handle_delivery(
        mq_channel,
        method,
        properties=properties,
        body=body,
        pg_conn=pg_conn,
        minio_client=minio_client,
        job_type="log_triage",
    )

    row = db.fetch_job_row(pg_conn, job_id)
    assert row["status"] == "COMPLETED"
    assert row["completed_at"] is not None

    artifact_key = f"jobs/{job_id}/result.json"
    with tempfile.TemporaryDirectory() as tmp:
        fetched = pathlib.Path(tmp) / "result.json"
        minio_client.download_file(SETTINGS.minio_bucket_job_artifacts, artifact_key, str(fetched))
        result = json.loads(fetched.read_text())
        # Schema-required fields only (skills/log-triage-summary/SKILL.md /
        # master plan §29) — no stand-in-only fields like the old
        # `skill_invoked`, since a real model's phrasing isn't scripted.
        for field in ("summary", "likely_cause", "severity_signal", "recommended_action", "confidence"):
            assert field in result
        assert result["severity_signal"] in ("low", "medium", "high", "critical")

    # AI cost-optimization mission Phase 1: telemetry.json uploaded
    # alongside result.json for a real (non-mocked) sandbox run.
    telemetry_key = f"jobs/{job_id}/telemetry.json"
    with tempfile.TemporaryDirectory() as tmp:
        fetched = pathlib.Path(tmp) / "telemetry.json"
        minio_client.download_file(SETTINGS.minio_bucket_job_artifacts, telemetry_key, str(fetched))
        telemetry = json.loads(fetched.read_text())
        assert telemetry["model"] == "claude-sonnet-5"
        assert isinstance(telemetry.get("raw_input_bytes"), int)
        assert "escalated" in telemetry

    method, properties, body = mq_channel.basic_get("noc.events.log", auto_ack=True)
    assert method is not None
    event = json.loads(body)
    assert event["job_id"] == str(job_id)
    assert event["event"] in ("started", "progress", "completed")

    _delete_job_row(pg_conn, job_id)


def test_end_to_end_unsupported_job_type_goes_to_dlq(pg_conn, minio_client, mq_channel, monkeypatch):
    """As of Milestone 14 both real job_types (log_triage, daily_report)
    have skills wired — so this exercises UnsupportedJobType by publishing
    onto the real log_triage queue (so DLQ routing/binding is real) while
    monkeypatching away log_triage's input-filename mapping, forcing
    `_process_job`'s lookup to legitimately come back empty."""
    from noc_bridge import service as service_module

    monkeypatch.setitem(
        service_module._INPUT_FILENAME_BY_JOB_TYPE,
        "log_triage",
        None,
    )

    job_id = uuid.uuid4()
    _insert_job_row(pg_conn, job_id, "log_triage")

    payload = {
        "job_id": str(job_id),
        "job_type": "log_triage",
        "incident_id": None,
        "object_refs": [],
        "model": "claude-sonnet-5",
        "effort": "high",
        "skill_name": "not-a-real-skill",
        "skill_version": "1",
        "correlation_id": str(uuid.uuid4()),
        "attempt": 1,
    }

    service = BridgeService(SETTINGS)

    names_in = queue_names("log_triage")
    mq_channel.confirm_delivery()
    mq_channel.basic_publish(
        exchange="noc.jobs",
        routing_key=names_in["routing_key"],
        body=json.dumps(payload).encode("utf-8"),
    )
    method, properties, body = mq_channel.basic_get(names_in["main"])
    assert method is not None

    service._handle_delivery(
        mq_channel,
        method,
        properties=properties,
        body=body,
        pg_conn=pg_conn,
        minio_client=minio_client,
        job_type="log_triage",
    )

    row = db.fetch_job_row(pg_conn, job_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "UnsupportedJobType"

    names = queue_names("log_triage")
    method, properties, body = mq_channel.basic_get(names["dlq"], auto_ack=True)
    assert method is not None

    _delete_job_row(pg_conn, job_id)


@pytest.mark.skipif(
    not _LIVE_CLAUDE_TESTS,
    reason="invokes the real Claude Code CLI and spends real subscription "
    "usage — set NOC_BRIDGE_LIVE_CLAUDE_TESTS=1 to opt in and run this "
    "yourself (e.g. `NOC_BRIDGE_LIVE_CLAUDE_TESTS=1 python3 -m pytest "
    "tests/test_bridge.py::test_end_to_end_daily_report_job`)",
)
def test_end_to_end_daily_report_job(pg_conn, minio_client, mq_channel):
    job_id = uuid.uuid4()
    _insert_job_row(pg_conn, job_id, "daily_report")

    bucket = SETTINGS.minio_bucket_reports
    key = f"jobs-test/{job_id}/snapshot.json"
    snapshot = {
        "shift_id": "SHIFT-TEST",
        "shift_starts_at": "2026-09-09T00:00:00+00:00",
        "shift_ends_at": None,
        "incidents": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "display_id": "INC-TEST",
                "title": "Payments API timeout",
                "service": "payments-api",
                "environment": "production",
                "status": "resolved",
                "triggered_at": "2026-09-09T00:05:00+00:00",
                "recovered_at": "2026-09-09T00:20:00+00:00",
                "analysis": {
                    "summary": "Unhandled exception in request path.",
                    "likely_cause": "unhandled exception",
                    "severity_signal": "high",
                    "recommended_action": "escalate to on-call engineer",
                    "confidence": 0.87,
                },
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "snapshot.json"
        src.write_text(json.dumps(snapshot))
        sha = sha256_of_file(src)
        minio_client.upload_file(str(src), bucket, key)

    payload = {
        "job_id": str(job_id),
        "job_type": "daily_report",
        "incident_id": None,
        "object_refs": [{"bucket": bucket, "key": key, "sha256": sha}],
        "model": "claude-sonnet-5",
        "effort": "medium",
        "skill_name": "daily-alert-report",
        "skill_version": "1",
        "correlation_id": str(uuid.uuid4()),
        "attempt": 1,
    }

    service = BridgeService(SETTINGS)

    ensure_image_built(__import__("docker").from_env())

    names = queue_names("daily_report")
    mq_channel.confirm_delivery()
    mq_channel.basic_publish(
        exchange="noc.jobs",
        routing_key=names["routing_key"],
        body=json.dumps(payload).encode("utf-8"),
    )
    method, properties, body = mq_channel.basic_get(names["main"])
    assert method is not None

    service._handle_delivery(
        mq_channel,
        method,
        properties=properties,
        body=body,
        pg_conn=pg_conn,
        minio_client=minio_client,
        job_type="daily_report",
    )

    row = db.fetch_job_row(pg_conn, job_id)
    assert row["status"] == "COMPLETED"
    assert row["completed_at"] is not None

    artifact_key = f"reports/{job_id}/report.docx"
    with tempfile.TemporaryDirectory() as tmp:
        fetched = pathlib.Path(tmp) / "report.docx"
        minio_client.download_file(SETTINGS.minio_bucket_reports, artifact_key, str(fetched))
        # A real DOCX is a zip archive — just confirm it downloaded and is
        # non-trivial in size, not its exact prose (unscripted model output).
        assert fetched.stat().st_size > 1000

    _delete_job_row(pg_conn, job_id)
