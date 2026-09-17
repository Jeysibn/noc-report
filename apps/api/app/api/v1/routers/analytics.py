"""Milestone 16 — Analytics: real PostgreSQL aggregates replace the
Milestone 7 mock fixtures (`src/mock/fixtures/analytics.ts`).

Master plan §17 V1 scope: incidents per day, incidents per shift, alerts
by service, top recurring alert titles, recovered vs unresolved, analysis
job success/failure, report generation counts. "Top exception/error
signatures" is V1-listed too, but exception/error signature isn't a
modeled field anywhere in the schema (same gap noted for Milestone 15's
search index) — left out rather than fabricated, per rule 20. V2 metrics
(MTBA, semantic clusters, heatmaps, shift comparison, operator workload,
recovery-duration trends) are explicitly out of scope for this milestone.

Every aggregate here is a real query against `incidents`/`jobs`/`reports`
— no synthetic fallback data. An empty database just returns zeros/empty
series, visible truthfully as such (same "no fallback" precedent as
analysis.py/reports.py).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_permission
from app.models.models import Incident, Job, Report, Shift, ShiftDefinition, User
from app.report_metrics import generated_report_predicate, generated_report_time
from app.schemas.schemas import (
    AnalyticsSummaryOut,
    BarDatumOut,
    DonutSliceOut,
    ReportGenerationCountsOut,
    TopAlertTitleOut,
)

router = APIRouter(tags=["analytics"])

_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/analytics/summary", response_model=AnalyticsSummaryOut)
def analytics_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> AnalyticsSummaryOut:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_start - timedelta(days=6)
    window_end = today_start + timedelta(days=1)

    # --- incidents per day (seven calendar dates, including today) ---
    day_bucket = func.date_trunc("day", Incident.triggered_at)
    rows = db.execute(
        select(day_bucket.label("day"), func.count().label("n"))
        .where(Incident.triggered_at >= window_start, Incident.triggered_at < window_end)
        .group_by(day_bucket)
    ).all()
    counts_by_date = {row.day.date(): row.n for row in rows}
    incidents_by_day = [
        BarDatumOut(
            label=_DAY_LABELS[(window_start + timedelta(days=i)).weekday()],
            value=counts_by_date.get((window_start + timedelta(days=i)).date(), 0),
        )
        for i in range(7)
    ]

    # --- incidents per shift definition (all-time, grouped by definition name) ---
    shift_rows = db.execute(
        select(ShiftDefinition.name, func.count(Incident.id))
        .select_from(ShiftDefinition)
        .join(Shift, Shift.shift_definition_id == ShiftDefinition.id)
        .join(Incident, Incident.shift_id == Shift.id)
        .group_by(ShiftDefinition.name)
        .order_by(ShiftDefinition.name)
    ).all()
    incidents_by_shift = [BarDatumOut(label=name, value=n) for name, n in shift_rows]

    # --- alerts (incidents) by service, top 10 ---
    service_rows = db.execute(
        select(Incident.service, func.count(Incident.id).label("n"))
        .group_by(Incident.service)
        .order_by(func.count(Incident.id).desc())
        .limit(10)
    ).all()
    alerts_by_service = [BarDatumOut(label=service, value=n) for service, n in service_rows]

    # --- recovered vs investigating vs open ---
    status_rows = dict(
        db.execute(select(Incident.status, func.count(Incident.id)).group_by(Incident.status)).all()
    )
    recovered_vs_unresolved = [
        DonutSliceOut(label="Recovered", value=status_rows.get("recovered", 0)),
        DonutSliceOut(label="Investigating", value=status_rows.get("investigating", 0)),
        DonutSliceOut(label="Open", value=status_rows.get("open", 0)),
    ]

    # --- analysis job outcomes (log_triage jobs only) ---
    job_status_rows = dict(
        db.execute(
            select(Job.status, func.count(Job.id))
            .where(Job.job_type == "log_triage")
            .group_by(Job.status)
        ).all()
    )
    failed = job_status_rows.get("FAILED", 0) + job_status_rows.get("DEAD_LETTER", 0)
    analysis_job_outcomes = [
        DonutSliceOut(label="Completed", value=job_status_rows.get("COMPLETED", 0)),
        DonutSliceOut(label="Failed", value=failed),
    ]

    # --- top recurring alert titles (case-insensitive title match, >1 occurrence) ---
    title_key = func.lower(Incident.title)
    top_titles_rows = db.execute(
        select(func.min(Incident.title).label("title"), func.count().label("n"))
        .group_by(title_key)
        .having(func.count() > 1)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    top_recurring_alert_titles = [TopAlertTitleOut(title=title, count=n) for title, n in top_titles_rows]

    # --- report generation counts: this shift / today / this week ---
    week_start = today_start - timedelta(days=today_start.weekday())
    generated_join = select(func.count()).select_from(Report).join(Job, Job.id == Report.job_id).where(
        generated_report_predicate()
    )
    generation_time = generated_report_time()
    today_count = db.scalar(generated_join.where(generation_time >= today_start)) or 0
    week_count = db.scalar(generated_join.where(generation_time >= week_start)) or 0
    active_shift = db.scalar(select(Shift).where(Shift.state == "active").order_by(Shift.starts_at.desc()))
    shift_count = 0
    if active_shift is not None:
        shift_count = (
            db.scalar(
                select(func.count())
                .select_from(Report)
                .join(Job, Job.id == Report.job_id)
                .where(Report.shift_id == active_shift.id, generated_report_predicate())
            )
            or 0
        )
    report_generation_counts = ReportGenerationCountsOut(
        this_shift=shift_count, today=today_count, this_week=week_count
    )

    return AnalyticsSummaryOut(
        incidents_by_day=incidents_by_day,
        incidents_by_shift=incidents_by_shift,
        alerts_by_service=alerts_by_service,
        recovered_vs_unresolved=recovered_vs_unresolved,
        analysis_job_outcomes=analysis_job_outcomes,
        top_recurring_alert_titles=top_recurring_alert_titles,
        report_generation_counts=report_generation_counts,
    )
