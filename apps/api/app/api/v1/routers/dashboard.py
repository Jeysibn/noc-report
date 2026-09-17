"""Authoritative operator dashboard aggregates.

The dashboard is a current-shift view. Counts are computed in PostgreSQL so
the browser never has to infer operational truth from a paginated incident
list. A log is "awaiting analysis" until it has a current, completed run
with a usable result; queued, running, and failed runs remain actionable.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends

from app.db.session import get_db
from app.deps import require_permission
from app.models.models import AnalysisRun, Evidence, Incident, Job, Report, Shift, User
from app.schemas.schemas import DashboardSummaryOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryOut)
def dashboard_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("dashboard.read")),
) -> DashboardSummaryOut:
    shift_id = db.scalar(
        select(Shift.id)
        .where(Shift.state == "active")
        .order_by(Shift.starts_at.desc())
    )
    if shift_id is None:
        return DashboardSummaryOut(
            open_incidents=0,
            active_alerts=0,
            recovered_alerts=0,
            logs_awaiting_analysis=0,
            analyses_running=0,
            reports_generated_this_shift=0,
        )

    shift_filter = Incident.shift_id == shift_id
    active_log = select(Evidence.id).where(
        Evidence.incident_id == Incident.id,
        Evidence.evidence_type == "LOG",
        Evidence.lifecycle_state == "ACTIVE",
    ).exists()
    usable_analysis = select(AnalysisRun.id).join(Job, Job.id == AnalysisRun.job_id).where(
        AnalysisRun.incident_id == Incident.id,
        AnalysisRun.current.is_(True),
        Job.status == "COMPLETED",
        AnalysisRun.result_json.is_not(None),
    ).exists()
    running_analysis = select(AnalysisRun.id).join(Job, Job.id == AnalysisRun.job_id).where(
        AnalysisRun.incident_id == Incident.id,
        AnalysisRun.current.is_(True),
        Job.status == "PROCESSING",
    ).exists()

    open_incidents = db.scalar(
        select(func.count()).select_from(Incident).where(shift_filter, Incident.status != "recovered")
    ) or 0
    active_alerts = db.scalar(
        select(func.count()).select_from(Incident).where(shift_filter, Incident.status == "open")
    ) or 0
    recovered_alerts = db.scalar(
        select(func.count()).select_from(Incident).where(shift_filter, Incident.status == "recovered")
    ) or 0
    logs_awaiting = db.scalar(
        select(func.count()).select_from(Incident).where(shift_filter, active_log, ~usable_analysis)
    ) or 0
    analyses_running = db.scalar(
        select(func.count()).select_from(Incident).where(shift_filter, running_analysis)
    ) or 0
    reports = db.scalar(
        select(func.count()).select_from(Report).join(Job, Job.id == Report.job_id).where(
            Report.shift_id == shift_id,
            Job.status == "COMPLETED",
            Report.report_version_id.is_not(None),
        )
    ) or 0

    return DashboardSummaryOut(
        open_incidents=open_incidents,
        active_alerts=active_alerts,
        recovered_alerts=recovered_alerts,
        logs_awaiting_analysis=logs_awaiting,
        analyses_running=analyses_running,
        reports_generated_this_shift=reports,
    )
