"""Database-level AnalysisRun.current invariant tests."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.models.models import AnalysisRun, Incident, Job
from app.skills.registry import resolve_active_snapshot
from tests.conftest import TestSessionLocal
from tests.test_shifts import _create_active_shift


def _job_and_run(incident_id, snapshot_id):
    session = TestSessionLocal()
    try:
        job = Job(
            job_type="log_triage",
            incident_id=incident_id,
            skill_snapshot_id=snapshot_id,
            correlation_id=str(uuid.uuid4()),
        )
        session.add(job)
        session.flush()
        session.add(AnalysisRun(
            incident_id=incident_id,
            job_id=job.id,
            skill_snapshot_id=snapshot_id,
            current=True,
        ))
        session.commit()
        return "committed"
    except IntegrityError:
        session.rollback()
        return "conflict"
    finally:
        session.close()


def test_current_analysis_run_is_unique_under_concurrent_inserts(db_session):
    shift = _create_active_shift(db_session)
    incident = Incident(
        display_id="INC-CURRENT-1", shift_id=shift.id, title="Current run race",
        service="api", environment="production", triggered_at=datetime.now(timezone.utc),
    )
    db_session.add(incident)
    snapshot = resolve_active_snapshot(db_session, "log-triage-summary")
    db_session.commit()
    incident_id = incident.id
    snapshot_id = snapshot.id

    barrier = threading.Barrier(2)
    results = []

    def attempt():
        barrier.wait()
        results.append(_job_and_run(incident_id, snapshot_id))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["committed", "conflict"]
    assert db_session.query(AnalysisRun).filter(
        AnalysisRun.incident_id == incident.id, AnalysisRun.current.is_(True)
    ).count() == 1
