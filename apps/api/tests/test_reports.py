"""Report runtime-boundary and historical artifact access coverage."""
from datetime import datetime, timezone

from app.models.models import Job, OutboxEvent, Report, ReportSnapshot
from app.core.config import settings
from tests.conftest import auth_headers, make_user
from tests.test_shifts import _create_active_shift


def test_new_report_returns_runtime_unavailable_without_freezing_or_enqueuing(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    shift = _create_active_shift(db_session)

    response = client.post(f"/api/v1/shifts/{shift.id}/reports", json={}, headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_RUNTIME_UNAVAILABLE"
    assert db_session.query(ReportSnapshot).count() == 0
    assert db_session.query(Report).count() == 0
    assert db_session.query(Job).count() == 0
    assert db_session.query(OutboxEvent).count() == 0


def test_report_generation_requires_permission(client, db_session):
    shift = _create_active_shift(db_session)
    response = client.post(f"/api/v1/shifts/{shift.id}/reports", json={})
    assert response.status_code == 401


def test_daily_report_remains_gated_during_phase_one(client, db_session, monkeypatch):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    shift = _create_active_shift(db_session)
    monkeypatch.setattr(settings, "ai_runtime", "hermes")
    monkeypatch.setattr(settings, "daily_report_ai_enabled", False)

    response = client.post(f"/api/v1/shifts/{shift.id}/reports", json={}, headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DAILY_REPORT_RUNTIME_UNAVAILABLE"
    assert db_session.query(Job).count() == 0


def test_historical_report_remains_readable(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    shift = _create_active_shift(db_session)
    snapshot = ReportSnapshot(
        shift_id=shift.id,
        snapshot_json={"incidents": []},
        sha256="b" * 64,
    )
    db_session.add(snapshot)
    db_session.flush()
    job = Job(
        job_type="daily_report",
        status="COMPLETED",
        skill_snapshot_id=None,
        correlation_id="historical-report",
        completed_at=datetime.now(timezone.utc),
    )
    # Existing rows may predate a skill snapshot FK; use a current snapshot
    # from the registry only when the schema requires it for new fixtures.
    from app.skills.registry import resolve_active_snapshot

    job.skill_snapshot_id = resolve_active_snapshot(db_session, "daily-alert-report").id
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Report(
            shift_id=shift.id,
            snapshot_id=snapshot.id,
            job_id=job.id,
            version=1,
            report_bucket="noc-reports",
            report_object_key="reports/historical/report.docx",
            report_version_id="report-version-1",
            report_sha256="c" * 64,
            report_byte_size=10,
            report_content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            generated_at=job.completed_at,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/shifts/{shift.id}/reports", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["downloadable"] is True
