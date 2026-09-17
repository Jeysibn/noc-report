"""Milestone 16 (Analytics) — real PostgreSQL aggregates, no mocked
fixtures. `analytics.read` is DevOps/Admin only per seed.py."""
from datetime import datetime, timezone

from tests.conftest import auth_headers, make_user
from app.models.models import AnalysisRun, Evidence, Incident, Job, Report, ReportSnapshot, SkillSnapshot
import uuid
from tests.test_shifts import _create_active_shift


def _create_incident(client, headers, **overrides) -> dict:
    body = {
        "title": "Payments API timeout",
        "service": "payments-api",
        "environment": "production",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }
    body.update(overrides)
    resp = client.post("/api/v1/incidents", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_analytics_requires_permission(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    resp = client.get("/api/v1/analytics/summary", headers=headers)
    assert resp.status_code == 403


def test_analytics_summary_shape_and_counts(client, db_session):
    make_user(db_session, "devops1", "DevOps")
    headers = auth_headers(client, "devops1")

    _create_incident(client, headers, title="Payments API timeout", service="payments-api")
    _create_incident(client, headers, title="Payments API timeout", service="payments-api")
    _create_incident(client, headers, title="Auth token validation failure", service="auth-service")

    resp = client.get("/api/v1/analytics/summary", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["incidents_by_day"]) == 8
    assert sum(row["value"] for row in body["incidents_by_day"]) == 3

    service_values = {row["label"]: row["value"] for row in body["alerts_by_service"]}
    assert service_values["payments-api"] == 2
    assert service_values["auth-service"] == 1

    recovered = {row["label"]: row["value"] for row in body["recovered_vs_unresolved"]}
    assert recovered["Open"] == 3

    top_titles = {row["title"]: row["count"] for row in body["top_recurring_alert_titles"]}
    assert top_titles["Payments API timeout"] == 2
    assert "Auth token validation failure" not in top_titles  # occurs once, not "recurring"

    assert body["report_generation_counts"] == {"this_shift": 0, "today": 0, "this_week": 0}
    assert body["analysis_job_outcomes"] == [
        {"label": "Completed", "value": 0},
        {"label": "Failed", "value": 0},
    ]


def test_dashboard_summary_aggregates_active_shift_without_page_limit(client, db_session):
    make_user(db_session, "dashboard-noc", "NOC")
    headers = auth_headers(client, "dashboard-noc")
    shift = _create_active_shift(db_session)
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Incident(
            display_id=f"INC-DASH-{i}", shift_id=shift.id, title="Alert", service="api",
            environment="production", status="open", triggered_at=now,
        )
        for i in range(201)
    ])
    db_session.commit()

    response = client.get("/api/v1/dashboard/summary", headers=headers)
    assert response.status_code == 200
    assert response.json()["open_incidents"] == 201
    assert response.json()["active_alerts"] == 201
    assert response.json()["reports_generated_this_shift"] == 0


def test_dashboard_summary_counts_only_usable_analysis_and_completed_report_artifacts(client, db_session):
    make_user(db_session, "dashboard-semantics", "NOC")
    headers = auth_headers(client, "dashboard-semantics")
    shift = _create_active_shift(db_session)
    skill = SkillSnapshot(
        skill_name="log-triage-summary", version_label=1, content_hash="d" * 64,
        skill_md="skill", output_schema_json="{}", manifest_yaml="id: log-triage-summary",
        dependency_snapshot_ids={}, is_active=True,
    )
    db_session.add(skill)
    db_session.flush()

    def add_log_incident(number: int, job_status: str | None = None, *, usable: bool = False) -> None:
        incident = Incident(
            display_id=f"INC-SEM-{number}", shift_id=shift.id, title="Alert", service="api",
            environment="production", status="open", triggered_at=datetime.now(timezone.utc),
        )
        db_session.add(incident)
        db_session.flush()
        db_session.add(Evidence(
            incident_id=incident.id, evidence_type="LOG", bucket="noc-evidence",
            object_key=f"logs/{number}.log", original_filename=f"{number}.log",
            lifecycle_state="ACTIVE",
        ))
        if job_status is not None:
            job = Job(
                job_type="log_triage", status=job_status, skill_snapshot_id=skill.id,
                skill_name=skill.skill_name, skill_version="1", correlation_id=str(uuid.uuid4()),
            )
            db_session.add(job)
            db_session.flush()
            db_session.add(AnalysisRun(
                incident_id=incident.id, job_id=job.id, current=True,
                result_json={"summary": "usable"} if usable else None,
            ))

    # No run, queued, failed, and running logs are all actionable. Only the
    # current completed run with a result is considered analyzed.
    add_log_incident(1)
    add_log_incident(2, "QUEUED")
    add_log_incident(3, "FAILED")
    add_log_incident(4, "PROCESSING")
    add_log_incident(5, "COMPLETED", usable=True)

    for number, job_status, artifact_version in (
        (1, "QUEUED", None), (2, "PROCESSING", None), (3, "FAILED", None),
        (4, "COMPLETED", "report-version-1"),
    ):
        job = Job(
            job_type="daily_report", status=job_status, skill_snapshot_id=skill.id,
            skill_name="daily-alert-report", skill_version="1", correlation_id=str(uuid.uuid4()),
        )
        db_session.add(job)
        db_session.flush()
        snapshot = ReportSnapshot(shift_id=shift.id, snapshot_json={}, sha256=(str(number) + "e" * 63)[:64], skill_snapshot_id=skill.id)
        db_session.add(snapshot)
        db_session.flush()
        db_session.add(Report(
            shift_id=shift.id, snapshot_id=snapshot.id, job_id=job.id, version=number,
            status=job_status, report_version_id=artifact_version,
        ))
    db_session.commit()

    response = client.get("/api/v1/dashboard/summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["logs_awaiting_analysis"] == 4
    assert body["analyses_running"] == 1
    assert body["reports_generated_this_shift"] == 1
