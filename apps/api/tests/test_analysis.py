"""Analysis boundary and historical AnalysisRun coverage."""
from datetime import datetime, timezone

from app.models.models import AnalysisRun, Evidence, Job, OutboxEvent
from app.skills.registry import resolve_active_snapshot
from tests.conftest import auth_headers, make_user


def _create_incident(client, headers) -> str:
    response = client.post(
        "/api/v1/incidents",
        json={
            "title": "Payments API timeout",
            "service": "payments-api",
            "environment": "production",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _attach_log(db_session, incident_id: str) -> None:
    db_session.add(
        Evidence(
            incident_id=incident_id,
            evidence_type="LOG",
            bucket="noc-evidence",
            object_key=f"incidents/{incident_id}/log.txt",
            original_filename="app.log",
            mime_type="text/plain",
            byte_size=12,
            sha256="a" * 64,
            version_id="version-1",
            lifecycle_state="ACTIVE",
        )
    )
    db_session.commit()


def test_new_analysis_returns_runtime_unavailable_without_creating_work(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    _attach_log(db_session, incident_id)

    response = client.post(
        f"/api/v1/incidents/{incident_id}/analysis-runs",
        json={},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_RUNTIME_UNAVAILABLE"
    assert db_session.query(Job).count() == 0
    assert db_session.query(OutboxEvent).count() == 0


def test_missing_log_keeps_domain_precondition(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)

    response = client.post(
        f"/api/v1/incidents/{incident_id}/analysis-runs",
        json={},
        headers=headers,
    )

    assert response.status_code == 409
    assert db_session.query(Job).count() == 0


def test_historical_analysis_remains_readable(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    incident_id = _create_incident(client, headers)
    snapshot = resolve_active_snapshot(db_session, "log-triage-summary")
    job = Job(
        job_type="log_triage",
        status="COMPLETED",
        incident_id=incident_id,
        skill_snapshot_id=snapshot.id,
        skill_name="log-triage-summary",
        skill_version="1",
        model="historical-runtime",
        correlation_id="historical-analysis",
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        AnalysisRun(
            incident_id=incident_id,
            job_id=job.id,
            result_json={"summary_en": "Historical result", "summary_zh": "历史结果"},
            result_text="Historical result",
            model="historical-runtime",
            skill_name="log-triage-summary",
            skill_version="1",
            skill_snapshot_id=snapshot.id,
            current=True,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/incidents/{incident_id}/analysis-runs", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["result"]["summary_en"] == "Historical result"
