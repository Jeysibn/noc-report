"""Milestone 13 — Real Log Triage (master plan pipeline):

    Analyze Log -> API -> PostgreSQL Job -> RabbitMQ -> Bridge -> Docker
    -> Claude Code -> log-triage-summary -> MinIO/PostgreSQL -> UI

No fallback analyzer in the API — if the bridge/sandbox pipeline isn't
running, a request just stays QUEUED (visible truthfully as such), it
never fabricates a result.
"""
import json
import uuid

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.config import settings
from app.core.storage import get_object_bytes, sha256_of_bytes
from app.db.session import get_db
from app.deps import require_permission
from app.jobs import enqueue_job, open_channel
from app.models.models import AnalysisRun, Evidence, Incident, Job, User
from app.schemas.schemas import AnalysisRequest, AnalysisRunOut

router = APIRouter(tags=["analysis"])

SKILL_NAME = "log-triage-summary"
SKILL_VERSION = "1"


def _get_incident_or_404(db: Session, incident_id: uuid.UUID) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


def _to_out(job: Job, run: AnalysisRun | None) -> AnalysisRunOut:
    return AnalysisRunOut(
        job_id=job.id,
        incident_id=job.incident_id,
        status=job.status,
        model=job.model,
        effort=job.effort,
        skill_name=job.skill_name,
        skill_version=job.skill_version,
        attempt=job.attempt,
        error_code=job.error_code,
        error_message=job.error_message,
        queued_at=job.queued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        run_id=run.id if run else None,
        result=run.result_json if run else None,
        current=run.current if run else False,
    )


def _sync_completed_job(db: Session, job: Job) -> AnalysisRun | None:
    """A completed `log_triage` Job has its result sitting in
    `noc-job-artifacts` (the bridge never writes into Postgres directly —
    see bridge/noc_bridge/service.py). The first poll to observe
    status=="COMPLETED" pulls that result down and fills in the
    AnalysisRun row created at enqueue time; every later poll is a no-op
    since `result_json` is only ever set once (§22.9: "never overwrite
    old runs")."""
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.job_id == job.id))
    if run is None or run.result_json is not None:
        return run
    if job.status != "COMPLETED":
        return run

    key = f"jobs/{job.id}/result.json"
    try:
        body = get_object_bytes(settings.minio_bucket_job_artifacts, key)
    except ClientError:
        # Bridge marked the Job COMPLETED but the artifact isn't visible
        # yet (or was never written) — leave the run unfilled rather than
        # fabricate a result; the next poll tries again.
        return run

    run.result_json = json.loads(body)
    run.result_text = body.decode("utf-8", errors="replace")
    run.output_sha256 = sha256_of_bytes(body)
    db.flush()
    return run


@router.post(
    "/incidents/{incident_id}/analysis-runs",
    response_model=AnalysisRunOut,
    status_code=status.HTTP_201_CREATED,
)
def request_analysis(
    incident_id: uuid.UUID,
    body: AnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.analysis.execute")),
) -> AnalysisRunOut:
    incident = _get_incident_or_404(db, incident_id)

    log_evidence = db.scalar(
        select(Evidence)
        .where(Evidence.incident_id == incident.id, Evidence.evidence_type == "LOG")
        .order_by(Evidence.created_at.desc())
    )
    if log_evidence is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Incident has no LOG evidence attached — upload a log before requesting analysis.",
        )

    object_refs = [
        {"bucket": log_evidence.bucket, "key": log_evidence.object_key, "sha256": log_evidence.sha256}
    ]

    with open_channel() as channel:
        job = enqueue_job(
            db,
            channel,
            job_type="log_triage",
            requested_by=current_user.id,
            incident_id=str(incident.id),
            object_refs=object_refs,
            model=body.model,
            effort=body.effort,
            skill_name=SKILL_NAME,
            skill_version=SKILL_VERSION,
        )

    # §22.9: created immediately (not on completion) so input provenance
    # (which evidence, its checksum) is captured against what was
    # actually submitted, not reconstructed later.
    db.execute(update(AnalysisRun).where(AnalysisRun.incident_id == incident.id).values(current=False))
    run = AnalysisRun(
        incident_id=incident.id,
        job_id=job.id,
        log_evidence_id=log_evidence.id,
        model=job.model,
        effort=job.effort,
        skill_name=job.skill_name,
        skill_version=job.skill_version,
        input_manifest_sha256=log_evidence.sha256,
        current=True,
    )
    db.add(run)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.analysis.execute",
        resource_type="incident",
        resource_id=str(incident.id),
        metadata={"job_id": str(job.id)},
    )
    db.commit()
    db.refresh(job)
    db.refresh(run)
    return _to_out(job, run)


@router.get("/incidents/{incident_id}/analysis-runs", response_model=list[AnalysisRunOut])
def list_analysis_runs(
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> list[AnalysisRunOut]:
    _get_incident_or_404(db, incident_id)

    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.incident_id == incident_id, Job.job_type == "log_triage")
            .order_by(Job.queued_at.desc())
        )
    )
    out = []
    for job in jobs:
        run = _sync_completed_job(db, job)
        out.append(_to_out(job, run))
    db.commit()
    return out


@router.get("/incidents/{incident_id}/analysis-runs/{job_id}", response_model=AnalysisRunOut)
def get_analysis_run(
    incident_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> AnalysisRunOut:
    _get_incident_or_404(db, incident_id)
    job = db.scalar(
        select(Job).where(Job.id == job_id, Job.incident_id == incident_id, Job.job_type == "log_triage")
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found")

    run = _sync_completed_job(db, job)
    db.commit()
    return _to_out(job, run)
