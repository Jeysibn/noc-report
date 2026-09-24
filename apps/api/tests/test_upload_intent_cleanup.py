from datetime import datetime, timedelta, timezone
import uuid

from botocore.exceptions import ClientError
import pytest

from app.core import config as config_module
from app.core.storage import delete_object_versions, evidence_object_key
from app.evidence_purge_worker import sweep_abandoned_upload_intents
from app.models.models import Evidence, EvidenceUploadIntent, Incident, User


def _expired_intent(db_session, *, state="OPEN", object_key=None):
    user = User(username=f"cleanup-{uuid.uuid4().hex[:8]}", display_name="Cleanup", password_hash="test")
    incident = Incident(
        display_id=f"INC-{uuid.uuid4().hex[:8]}",
        title="Upload cleanup",
        service="test",
        environment="test",
        triggered_at=datetime.now(timezone.utc),
    )
    db_session.add_all([user, incident])
    db_session.flush()
    intent_id = uuid.uuid4()
    key = evidence_object_key(str(incident.id), "LOG", "orphan.log", intent_id)
    intent = EvidenceUploadIntent(
        id=intent_id,
        incident_id=incident.id,
        created_by=user.id,
        bucket=config_module.settings.minio_bucket_evidence,
        object_key=object_key or key,
        evidence_type="LOG",
        original_filename="orphan.log",
        state=state,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(intent)
    db_session.commit()
    return intent


def test_expired_intent_removes_exact_uploaded_version(db_session, monkeypatch):
    intent = _expired_intent(db_session)
    removed = []
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda bucket, key: removed.append((bucket, key)) or 2)

    assert sweep_abandoned_upload_intents(db_session) == 1
    db_session.refresh(intent)
    assert intent.state == "CLEANED"
    assert removed == [(intent.bucket, intent.object_key)]


def test_expired_intent_without_object_is_settled(db_session, monkeypatch):
    intent = _expired_intent(db_session)
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_args: 0)

    assert sweep_abandoned_upload_intents(db_session) == 1
    db_session.refresh(intent)
    assert intent.state == "CLEANED"


def test_completed_intent_is_never_cleaned(db_session, monkeypatch):
    intent = _expired_intent(db_session, state="COMPLETED")
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_: pytest.fail("completed intent must be ignored"))
    assert sweep_abandoned_upload_intents(db_session) == 0
    db_session.refresh(intent)
    assert intent.state == "COMPLETED"


def test_wrong_server_storage_identity_is_never_deleted(db_session, monkeypatch):
    intent = _expired_intent(db_session, object_key="incidents/someone-elses-object")
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_: pytest.fail("identity mismatch must not touch storage"))
    assert sweep_abandoned_upload_intents(db_session) == 0
    db_session.refresh(intent)
    assert intent.state == "EXPIRED"


def test_intent_object_referenced_by_evidence_is_never_deleted(db_session, monkeypatch):
    intent = _expired_intent(db_session)
    db_session.add(Evidence(
        incident_id=intent.incident_id,
        evidence_type=intent.evidence_type,
        bucket=intent.bucket,
        object_key=intent.object_key,
        original_filename=intent.original_filename,
        lifecycle_state="ACTIVE",
    ))
    db_session.commit()
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_: pytest.fail("referenced object must be preserved"))

    assert sweep_abandoned_upload_intents(db_session) == 1
    db_session.refresh(intent)
    assert intent.state == "COMPLETED"
    assert intent.evidence_id is not None


def test_version_cleanup_deletes_only_exact_key_versions(monkeypatch):
    class Paginator:
        def paginate(self, **kwargs):
            assert kwargs == {"Bucket": "noc-evidence", "Prefix": "intent-key"}
            return [{
                "Versions": [
                    {"Key": "intent-key", "VersionId": "v1"},
                    {"Key": "intent-key-sibling", "VersionId": "sibling"},
                ],
                "DeleteMarkers": [{"Key": "intent-key", "VersionId": "marker"}],
            }]

    class Client:
        def __init__(self):
            self.deleted = []

        def get_paginator(self, name):
            assert name == "list_object_versions"
            return Paginator()

        def delete_object(self, **kwargs):
            self.deleted.append(kwargs)

    client = Client()
    monkeypatch.setattr("app.core.storage.get_client", lambda: client)
    assert delete_object_versions("noc-evidence", "intent-key") == 2
    assert client.deleted == [
        {"Bucket": "noc-evidence", "Key": "intent-key", "VersionId": "v1"},
        {"Bucket": "noc-evidence", "Key": "intent-key", "VersionId": "marker"},
    ]


def test_minio_failure_keeps_intent_retryable(db_session, monkeypatch):
    intent = _expired_intent(db_session)
    unavailable = ClientError({"Error": {"Code": "SlowDown"}}, "HeadObject")
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_: (_ for _ in ()).throw(unavailable))
    assert sweep_abandoned_upload_intents(db_session) == 0
    db_session.refresh(intent)
    assert intent.state == "OPEN"


def test_database_commit_failure_retries_missing_object_idempotently(db_session, monkeypatch):
    intent = _expired_intent(db_session)
    deleted = []
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_: deleted.append("v-2") or 1)
    real_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    assert sweep_abandoned_upload_intents(db_session) == 0
    monkeypatch.setattr(db_session, "commit", real_commit)
    monkeypatch.setattr("app.evidence_purge_worker.delete_object_versions", lambda *_: 0)

    assert sweep_abandoned_upload_intents(db_session) == 1
    db_session.refresh(intent)
    assert intent.state == "CLEANED"
    assert deleted == ["v-2"]
