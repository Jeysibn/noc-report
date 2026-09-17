from __future__ import annotations

import io
import uuid
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from noc_bridge.config import BridgeSettings
from noc_bridge.service import (
    BridgeService,
    _reconcile_artifacts,
    _renderer_profile_from_snapshot,
)


class _FakeBody(io.BytesIO):
    pass


class _FakeMinio:
    def __init__(self, job_id: uuid.UUID):
        self.objects = {
            ("noc-reports", f"reports/{job_id}/report.docx"): b"docx-v1",
            ("noc-reports", f"reports/{job_id}/document.json"): b'{"title":"v1"}',
            ("noc-reports", f"reports/{job_id}/document.screenshots.json"): b"[]",
        }

    def head_object(self, *, Bucket: str, Key: str):
        try:
            body = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject") from exc
        return {"VersionId": f"version-{Key.rsplit('/', 1)[-1]}", "ContentType": "application/json", "ContentLength": len(body)}

    def get_object(self, *, Bucket: str, Key: str, VersionId: str | None = None):
        return {"Body": _FakeBody(self.objects[(Bucket, Key)])}


def test_report_reconciliation_reconstructs_all_artifact_identities_without_execution():
    job_id = uuid.uuid4()
    settings = BridgeSettings()
    client = _FakeMinio(job_id)

    metadata = _reconcile_artifacts("daily_report", job_id, settings, client)

    assert metadata is not None
    assert set(metadata) == {"report", "document", "screenshots"}
    for artifact in metadata.values():
        assert artifact["version_id"]
        assert len(artifact["sha256"]) == 64
        assert artifact["byte_size"] > 0


def test_partial_structured_report_artifact_is_not_marked_complete():
    job_id = uuid.uuid4()
    settings = BridgeSettings()
    client = _FakeMinio(job_id)
    del client.objects[("noc-reports", f"reports/{job_id}/document.screenshots.json")]

    assert _reconcile_artifacts("daily_report", job_id, settings, client) is None


def test_renderer_profile_controls_required_artifacts():
    job_id = uuid.uuid4()
    settings = BridgeSettings()
    client = _FakeMinio(job_id)
    del client.objects[("noc-reports", f"reports/{job_id}/document.json")]
    del client.objects[("noc-reports", f"reports/{job_id}/document.screenshots.json")]

    assert _reconcile_artifacts(
        "daily_report", job_id, settings, client,
        renderer_profile="report-document-v1",
    ) is None
    legacy = _reconcile_artifacts(
        "daily_report", job_id, settings, client,
        renderer_profile="daily_report_docx",
    )
    assert legacy is not None
    assert set(legacy) == {"report"}


class _SnapshotCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        return None

    def fetchone(self):
        return self.row


class _SnapshotConnection:
    def __init__(self, row):
        self.row = row

    def cursor(self, **_kwargs):
        return _SnapshotCursor(self.row)


def test_reconciliation_profile_comes_from_frozen_snapshot_not_live_checkout(tmp_path):
    skill_dir = tmp_path / "daily-alert-report"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text("renderer_profile: daily_report_docx\n")
    settings = BridgeSettings(skills_dir=tmp_path)
    payload = {
        "skill_name": "daily-alert-report",
        "skill_hash": "snapshot-hash",
        "skill_snapshot_id": "snapshot-id",
    }
    conn = _SnapshotConnection({
        "manifest_yaml": "renderer_profile: report-document-v1\n",
    })

    assert _renderer_profile_from_snapshot(
        conn, payload, settings, "daily-alert-report"
    ) == "report-document-v1"


def test_redelivered_complete_report_returns_before_second_claude_invocation(monkeypatch):
    job_id = uuid.uuid4()
    settings = BridgeSettings()
    client = _FakeMinio(job_id)
    channel = Mock()
    mark_completed = Mock()
    monkeypatch.setattr("noc_bridge.service.db.load_system_config", lambda _conn: {
        "default_model": "claude-sonnet-5",
        "default_effort": "low",
        "job_timeout_seconds": 900,
        "max_concurrent_jobs": 1,
        "claude_max_budget_usd": 0.5,
    })
    monkeypatch.setattr("noc_bridge.service.db.mark_completed", mark_completed)
    monkeypatch.setattr("noc_bridge.service.publish_status_event", Mock())
    monkeypatch.setattr(
        "noc_bridge.service.run_job_sandbox",
        lambda *args, **kwargs: pytest.fail("reconciliation must not invoke Claude"),
    )

    BridgeService(settings)._process_job(
        "daily_report", {"job_id": str(job_id), "attempt": 2},
        pg_conn=Mock(), minio_client=client, channel=channel,
    )

    mark_completed.assert_called_once()
    metadata = mark_completed.call_args.kwargs["artifact_metadata"]
    assert set(metadata) == {"report", "document", "screenshots"}
