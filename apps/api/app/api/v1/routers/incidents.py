import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.db.session import get_db
from app.deps import require_permission
from app.evidence_lifecycle import EvidenceReferencedError, mark_purged, purge_storage, request_purge
from app.incident_scope import shift_incident_statement
from app.models.models import AnalysisRun, Evidence, EvidenceUploadIntent, Incident, Job, OcrRun, OutboxEvent, Shift, User
from app.schemas.schemas import (
    IncidentCreate,
    IncidentOut,
    IncidentPage,
    IncidentReadinessOut,
    IncidentTimelineEventOut,
    IncidentUpdate,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _next_display_id(db: Session) -> str:
    """Derives the next display_id from the highest numeric suffix already
    in use, not a row count — a row count drifts out of sync with reality
    the moment any incident is deleted (or created outside the normal flow),
    producing a duplicate display_id and an IntegrityError on insert. Parsed
    in Python rather than with a DB-side cast so a non-numeric/legacy
    display_id can't blow up the query."""
    existing = db.scalars(select(Incident.display_id).where(Incident.display_id.like("INC-%"))).all()
    highest = 1000
    for display_id in existing:
        suffix = display_id.removeprefix("INC-")
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"INC-{highest + 1}"


_STATUS_TO_ANALYSIS_STATUS = {
    "COMPLETED": "completed",
    "FAILED": "failed",
    "QUEUED": "queued",
    "RETRYING": "retrying",
}


def _attach_derived(db: Session, incidents: list[Incident]) -> list[IncidentOut]:
    """Batches the has_log/analysis_status lookup across every incident in
    `incidents` into two queries total, regardless of list size — so
    list_incidents stays cheap while no longer reporting the hardcoded
    has_log=False/analysis_status="not_analyzed" the frontend was
    previously stuck with for every row (it only got the real answer from
    get_incident, a per-row lookup only affordable on the single-incident
    page)."""
    ids = [i.id for i in incidents]
    if not ids:
        return []

    log_incident_ids = set(
        db.scalars(
            select(Evidence.incident_id)
            .where(
                Evidence.incident_id.in_(ids),
                Evidence.evidence_type == "LOG",
                Evidence.lifecycle_state == "ACTIVE",
            )
            .distinct()
        )
    )

    status_by_incident: dict[uuid.UUID, str] = {}
    for incident_id, job_status in db.execute(
        select(AnalysisRun.incident_id, Job.status)
        .join(Job, Job.id == AnalysisRun.job_id)
        .where(AnalysisRun.incident_id.in_(ids), AnalysisRun.current.is_(True))
    ):
        status_by_incident[incident_id] = _STATUS_TO_ANALYSIS_STATUS.get(job_status, "running")

    out = []
    for incident in incidents:
        out.append(
            IncidentOut.model_validate(incident).model_copy(
                update={
                    "has_log": incident.id in log_incident_ids,
                    "analysis_status": status_by_incident.get(incident.id, "not_analyzed"),
                }
            )
        )
    return out


@router.get("", response_model=IncidentPage)
def list_incidents(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
    # A zero limit explicitly requests the complete report/readiness scope;
    # ordinary incident lists retain the safe default page size.
    limit: int = Query(default=25, ge=0, le=2000),
    offset: int = Query(default=0, ge=0),
    shift_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    service: str | None = None,
    environment: str | None = None,
    has_log: bool | None = Query(default=None, alias="has_log"),
) -> IncidentPage:
    stmt = shift_incident_statement(shift_id) if shift_id else select(Incident)
    if status_filter:
        stmt = stmt.where(Incident.status == status_filter)
    if service:
        stmt = stmt.where(Incident.service == service)
    if environment:
        stmt = stmt.where(Incident.environment == environment)
    if has_log is not None:
        log_exists = exists(
            select(Evidence.id).where(
                Evidence.incident_id == Incident.id,
                Evidence.evidence_type == "LOG",
                Evidence.lifecycle_state == "ACTIVE",
            )
        )
        stmt = stmt.where(log_exists if has_log else ~log_exists)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(None).order_by(Incident.created_at if shift_id else Incident.triggered_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    items = list(db.scalars(stmt.offset(offset)))
    return IncidentPage(items=_attach_derived(db, items), total=total)


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(
    body: IncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.create")),
) -> IncidentOut:
    current_shift = db.scalar(select(Shift).where(Shift.state == "active"))

    if body.status == "recovered" and body.recovered_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Recovered incidents require recovered_at.")
    if body.status != "recovered" and body.recovered_at is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only recovered incidents may have recovered_at.")

    # Serialize the read-then-write display-id allocation at the transaction
    # level. The unique constraint remains the final guard, but advisory
    # locking prevents concurrent requests from repeatedly selecting the same
    # highest suffix and relying on collision retries.
    db.execute(text("SELECT pg_advisory_xact_lock(48190217)"))
    for attempt in range(2):
        incident = Incident(
            display_id=_next_display_id(db),
            shift_id=current_shift.id if current_shift else None,
            title=body.title,
            service=body.service,
            environment=body.environment,
            status=body.status,
            alert_source=body.alert_source,
            triggered_at=body.triggered_at,
            recovered_at=body.recovered_at,
            trigger_value=body.trigger_value,
            teams_url=body.teams_url,
            grafana_url=body.grafana_url,
            notes=body.notes,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(incident)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 1:
                raise
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.create",
        resource_type="incident",
        resource_id=str(incident.id),
        metadata={"display_id": incident.display_id},
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/readiness", response_model=list[IncidentReadinessOut])
def list_incident_readiness(
    shift_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> list[IncidentReadinessOut]:
    """Return only the fields needed by shift report polling.

    The full incident list remains available for normal browsing, while this
    narrow projection avoids repeatedly transferring notes, links, timestamps,
    and other incident details every few seconds just to render readiness.
    """
    incidents = list(
        db.scalars(
            select(Incident)
            .where(Incident.shift_id == shift_id)
            .order_by(Incident.created_at)
        )
    )
    return [
        IncidentReadinessOut(
            id=item.id,
            title=item.title,
            has_log=derived.has_log,
            analysis_status=derived.analysis_status,
        )
        for item, derived in zip(incidents, _attach_derived(db, incidents), strict=True)
    ]


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> IncidentOut:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return _attach_derived(db, [incident])[0]


@router.get("/{incident_id}/timeline", response_model=list[IncidentTimelineEventOut])
def get_incident_timeline(
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> list[IncidentTimelineEventOut]:
    """Real timeline, derived from existing timestamped rows rather than a
    separate event log — so it always reflects the true current state of
    the incident (including analysis jobs a runtime worker updates directly in
    Postgres) with no chance of a forgotten instrumentation point leaving
    a step permanently stuck."""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")

    events: list[IncidentTimelineEventOut] = [
        IncidentTimelineEventOut(
            event_type="triggered",
            label="Triggered",
            occurred_at=incident.triggered_at,
            detail={"alert_source": incident.alert_source} if incident.alert_source else None,
        ),
        IncidentTimelineEventOut(
            event_type="incident_created",
            label="Incident created",
            occurred_at=incident.created_at,
        ),
    ]

    for ev in db.scalars(
        select(Evidence).where(Evidence.incident_id == incident_id).order_by(Evidence.created_at)
    ):
        label = "Screenshot attached" if ev.evidence_type == "SCREENSHOT" else (
            "Log attached" if ev.evidence_type == "LOG" else f"{ev.evidence_type.title()} attached"
        )
        events.append(
            IncidentTimelineEventOut(
                event_type="evidence_attached",
                label=label,
                occurred_at=ev.created_at,
                detail={"evidence_type": ev.evidence_type, "filename": ev.original_filename},
            )
        )

    for job in db.scalars(
        select(Job).where(Job.incident_id == incident_id).order_by(Job.queued_at)
    ):
        kind = "analysis" if job.job_type == "log_triage" else "report generation"
        events.append(
            IncidentTimelineEventOut(
                event_type="job_requested",
                label=f"{kind.capitalize()} requested",
                occurred_at=job.queued_at,
                detail={"job_id": str(job.id), "job_type": job.job_type},
            )
        )
        if job.started_at is not None:
            events.append(
                IncidentTimelineEventOut(
                    event_type="job_started",
                    label=f"{kind.capitalize()} started",
                    occurred_at=job.started_at,
                    detail={"job_id": str(job.id), "job_type": job.job_type},
                )
            )
        if job.completed_at is not None or job.status == "RETRYING":
            if job.status == "COMPLETED":
                events.append(
                    IncidentTimelineEventOut(
                        event_type="job_completed",
                        label=f"{kind.capitalize()} completed",
                        occurred_at=job.completed_at,
                        detail={"job_id": str(job.id), "job_type": job.job_type},
                    )
                )
            elif job.status in {"FAILED", "RETRYING"}:
                events.append(
                    IncidentTimelineEventOut(
                        event_type="job_retrying" if job.status == "RETRYING" else "job_failed",
                        label=f"{kind.capitalize()} retrying" if job.status == "RETRYING" else f"{kind.capitalize()} failed",
                        occurred_at=job.completed_at or job.queued_at,
                        detail={
                            "job_id": str(job.id),
                            "job_type": job.job_type,
                            "error_code": job.error_code,
                            "error_message": job.error_message,
                        },
                    )
                )

    if incident.recovered_at is not None:
        events.append(
            IncidentTimelineEventOut(
                event_type="recovered",
                label="Recovered",
                occurred_at=incident.recovered_at,
            )
        )

    events.sort(key=lambda e: e.occurred_at)
    return events


@router.patch("/{incident_id}", response_model=IncidentOut)
def update_incident(
    incident_id: uuid.UUID,
    body: IncidentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.update")),
) -> IncidentOut:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")

    changes = body.model_dump(exclude_unset=True)
    next_status = changes.get("status", incident.status)
    next_recovered_at = changes.get("recovered_at", incident.recovered_at)
    if next_status == "recovered" and next_recovered_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Recovered incidents require recovered_at.")
    if next_status != "recovered" and next_recovered_at is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only recovered incidents may have recovered_at.")

    for field, value in changes.items():
        setattr(incident, field, value)
    incident.updated_by = current_user.id

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.update",
        resource_type="incident",
        resource_id=str(incident.id),
        metadata=body.model_dump(exclude_unset=True, mode="json"),
    )
    db.commit()
    db.refresh(incident)
    return _attach_derived(db, [incident])[0]


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_incident(
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.delete")),
) -> None:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")

    # Delete dependent execution rows, but retain evidence tombstones until
    # their exact MinIO versions have been safely cleaned up. Referenced
    # evidence is rejected above rather than silently destroying historical
    # report inputs.
    evidence_records = list(db.scalars(select(Evidence).where(Evidence.incident_id == incident_id)))
    try:
        purge_plans = [request_purge(db, evidence) for evidence in evidence_records]
    except EvidenceReferencedError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Incident evidence is retained: {exc}") from exc
    evidence_ids = [evidence.id for evidence in evidence_records]
    if evidence_ids:
        db.execute(delete(OcrRun).where(OcrRun.evidence_id.in_(evidence_ids)))
    job_ids = list(db.scalars(select(Job.id).where(Job.incident_id == incident_id)))
    if job_ids:
        # outbox_events.job_id FK (ADR 0006, added after this route was
        # first written) blocks deleting a Job until its dispatch
        # events are gone too — the outbox row's dispatch history has no
        # meaning once the job it describes no longer exists.
        db.execute(delete(OutboxEvent).where(OutboxEvent.job_id.in_(job_ids)))
    db.execute(delete(AnalysisRun).where(AnalysisRun.incident_id == incident_id))
    # Keep tombstones so storage cleanup can be retried after this
    # transaction. The incident foreign key is intentionally nullable for
    # this lifecycle state.
    db.execute(
        Evidence.__table__.update()
        .where(Evidence.incident_id == incident_id)
        .values(incident_id=None)
    )
    db.execute(delete(EvidenceUploadIntent).where(EvidenceUploadIntent.incident_id == incident_id))
    db.execute(delete(Job).where(Job.incident_id == incident_id))

    db.delete(incident)
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.delete",
        resource_type="incident",
        resource_id=str(incident_id),
        metadata={},
    )
    db.commit()
    for plan in purge_plans:
        try:
            purge_storage(plan)
        except Exception:
            # The PURGE_PENDING tombstone is durable and can be retried.
            continue
        mark_purged(db, plan.evidence_id)
