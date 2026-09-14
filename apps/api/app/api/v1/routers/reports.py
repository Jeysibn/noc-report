"""Milestone 14 — Real Daily Report (master plan pipeline):

    Generate Report -> freeze snapshot -> RabbitMQ -> Bridge -> Docker
    -> Claude Code -> daily-alert-report -> DOCX -> MinIO -> UI download

Mirrors Milestone 13's analysis.py pattern closely: no fallback report
generator — if the bridge/sandbox pipeline isn't running, a Report just
stays QUEUED (visible truthfully as such). The one real difference is
the freeze step: report generation reads from a `ReportSnapshot` row
created at request time, not from live incident/analysis state, so a
Report's content never drifts even if incidents are edited afterward.
"""
import json
import uuid

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.config import settings
from app.core.storage import (
    get_client,
    get_object_bytes,
    presigned_download_url,
    sha256_of_bytes,
)
from app.db.session import get_db
from app.deps import require_permission
from app.jobs import enqueue_job
from app.models.models import AnalysisRun, Evidence, Incident, Job, Report, ReportSnapshot, Shift, SkillSnapshot, User
from app.report_fragments import build_report_fragment
from app.skills.registry import compute_execution_hash, resolve_active_snapshot
from app.skills.runtime import declared_skill_version, execution_policy, load_manifest
from app.schemas.schemas import (
    ReportDownloadUrlResponse,
    ReportGenerateRequest,
    ReportOut,
)

router = APIRouter(tags=["reports"])

SKILL_NAME = "daily-alert-report"


def _get_shift_or_404(db: Session, shift_id: uuid.UUID) -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    return shift


def _build_snapshot(db: Session, shift: Shift, report_skill_snapshot) -> dict:
    """Real query over this shift's incidents and their current analysis
    run, frozen into one JSON document — this is what the skill actually
    sees, and what a completed Report's content is judged against later,
    however the underlying incidents change afterward."""
    incidents = list(
        db.scalars(select(Incident).where(Incident.shift_id == shift.id).order_by(Incident.created_at))
    )
    incident_rows = []
    for incident in incidents:
        run = db.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.incident_id == incident.id, AnalysisRun.current.is_(True))
        )
        analysis_presentation_contract = None
        if run and run.skill_snapshot_id:
            analysis_snapshot = db.get(SkillSnapshot, run.skill_snapshot_id)
            if analysis_snapshot is not None:
                analysis_manifest = load_manifest(analysis_snapshot)
                exports = analysis_manifest.get("exports") or {}
                presentation = exports.get("presentation") if isinstance(exports, dict) else None
                if isinstance(presentation, dict):
                    analysis_presentation_contract = presentation.get("contract")
        evidence_items = list(
            db.scalars(select(Evidence).where(Evidence.incident_id == incident.id))
        )
        log_evidence = next((e for e in evidence_items if e.evidence_type == "LOG"), None)
        screenshot_evidence = [
            e for e in evidence_items
            if e.evidence_type in ("ALERT_SCREENSHOT", "GRAFANA_SCREENSHOT")
        ]
        incident_rows.append(
            {
                "id": str(incident.id),
                "display_id": incident.display_id,
                "title": incident.title,
                "service": incident.service,
                "environment": incident.environment,
                "status": incident.status,
                "triggered_at": incident.triggered_at.isoformat(),
                "recovered_at": incident.recovered_at.isoformat() if incident.recovered_at else None,
                "trigger_value": incident.trigger_value,
                "teams_url": incident.teams_url,
                "grafana_url": incident.grafana_url,
                "log_filename": log_evidence.original_filename if log_evidence else None,
                "screenshots": [
                    {"bucket": e.bucket, "object_key": e.object_key, "filename": e.original_filename}
                    for e in screenshot_evidence
                ],
                "analysis": run.result_json if (run and run.result_json) else None,
                "analysis_presentation_contract": analysis_presentation_contract,
                "report_fragment": build_report_fragment(run.result_json if run else None),
                # Skill Runtime mission Phase 7: exact per-incident
                # dependency provenance, frozen into the snapshot
                # alongside the analysis content itself — which
                # AnalysisRun/SkillSnapshot/output produced this
                # incident's analysis, so a report can always be traced
                # back to the exact skill version and raw output that
                # fed it, even after the incident's current run changes.
                "analysis_run_id": str(run.id) if run else None,
                "analysis_log_evidence_id": str(run.log_evidence_id) if (run and run.log_evidence_id) else None,
                "analysis_skill_snapshot_id": str(run.skill_snapshot_id) if (run and run.skill_snapshot_id) else None,
                "analysis_skill_execution_hash": run.skill_execution_hash if run else None,
                "analysis_skill_hash": run.skill_hash if run else None,
                "analysis_skill_version": run.skill_version if run else None,
                "analysis_schema_hash": run.schema_hash if run else None,
                "analysis_input_contract_version": run.input_contract_version if run else None,
                "analysis_preprocessor_version": run.preprocessor_version if run else None,
                "analysis_ai_policy_version": run.ai_policy_version if run else None,
                "analysis_ai_policy": run.ai_policy_json if run else None,
                "analysis_input_manifest_sha256": run.input_manifest_sha256 if run else None,
                "analysis_model": run.model if run else None,
                "analysis_effort": run.effort if run else None,
                "analysis_attempt_count": run.attempt_count if run else None,
                "analysis_used_cache": run.used_cache if run else False,
                "analysis_cache_type": run.cache_type if run else None,
                "analysis_input_tokens": run.input_tokens if run else None,
                "analysis_output_tokens": run.output_tokens if run else None,
                "analysis_cache_read_tokens": run.cache_read_tokens if run else None,
                "analysis_cache_creation_tokens": run.cache_creation_tokens if run else None,
                "analysis_estimated_cost_usd": run.estimated_cost_usd if run else None,
                "analysis_output_sha256": run.output_sha256 if run else None,
                "analysis_total_model_input_tokens": run.total_model_input_tokens if run else None,
            }
        )
    return {
        "shift_id": str(shift.id),
        "shift_starts_at": shift.starts_at.isoformat(),
        "shift_ends_at": shift.ends_at.isoformat() if shift.ends_at else None,
        "report_skill_snapshot_id": str(report_skill_snapshot.id),
        "report_skill_execution_hash": compute_execution_hash(db, report_skill_snapshot),
        # Coverage is part of the immutable report-skill contract. The
        # bridge must validate against this frozen policy, never the current
        # checkout or a later activation.
        "coverage": load_manifest(report_skill_snapshot).get("coverage") or {},
        "incidents": incident_rows,
    }


def _report_object_key(job_id: uuid.UUID) -> str:
    return f"reports/{job_id}/report.docx"


def _to_out(report: Report, job: Job) -> ReportOut:
    return ReportOut(
        id=report.id,
        shift_id=report.shift_id,
        snapshot_id=report.snapshot_id,
        job_id=report.job_id,
        version=report.version,
        status=job.status,
        model=report.model,
        effort=report.effort,
        skill_name=report.skill_name,
        skill_version=report.skill_version,
        skill_snapshot_id=report.skill_snapshot_id,
        skill_hash=report.skill_hash,
        skill_execution_hash=report.skill_execution_hash,
        generated_by=report.generated_by,
        generated_at=report.generated_at,
        error_message=job.error_message,
        created_at=report.created_at,
        downloadable=bool(report.report_object_key),
    )


def _sync_completed_report(db: Session, report: Report, job: Job) -> None:
    """Same sync-once-on-poll shape as Milestone 13's analysis run sync,
    but the artifact here is a DOCX in noc-reports (not a JSON result in
    noc-job-artifacts) — the bridge writes it to a deterministic key this
    function re-derives rather than being told."""
    if report.report_object_key is not None or job.status != "COMPLETED":
        return

    key = _report_object_key(job.id)
    try:
        head = get_object_bytes(settings.minio_bucket_reports, key)
    except ClientError:
        # Bridge marked the Job COMPLETED but the DOCX isn't visible yet
        # (or was never written) — leave the report unfilled, try again
        # next poll, never fabricate a download.
        return

    report.report_bucket = settings.minio_bucket_reports
    report.report_object_key = key
    report.report_sha256 = sha256_of_bytes(head)
    report.generated_at = job.completed_at
    db.flush()


@router.post(
    "/shifts/{shift_id}/reports",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
)
def generate_report(
    shift_id: uuid.UUID,
    body: ReportGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("report.generate")),
) -> ReportOut:
    shift = _get_shift_or_404(db, shift_id)

    # Resolve before freezing the snapshot so report composition and the Job
    # carry one immutable report-skill identity from the same transaction.
    skill_snapshot = resolve_active_snapshot(db, SKILL_NAME)
    skill_execution_hash = compute_execution_hash(db, skill_snapshot)
    snapshot_json = _build_snapshot(db, shift, skill_snapshot)
    snapshot_bytes = json.dumps(snapshot_json).encode("utf-8")
    snapshot_sha256 = sha256_of_bytes(snapshot_bytes)

    snapshot = ReportSnapshot(
        shift_id=shift.id,
        snapshot_json=snapshot_json,
        sha256=snapshot_sha256,
        skill_snapshot_id=skill_snapshot.id,
        skill_execution_hash=skill_execution_hash,
        created_by=current_user.id,
    )
    db.add(snapshot)
    db.flush()

    # The snapshot has to live in MinIO (not just Postgres JSON) so the
    # bridge can download+checksum-verify it into the sandbox through the
    # same object_refs path Milestone 13 already built — no separate
    # "small JSON inline in the queue message" code path to maintain.
    snapshot_key = f"snapshots/{shift.id}/{snapshot.id}.json"
    get_client().put_object(
        Bucket=settings.minio_bucket_reports,
        Key=snapshot_key,
        Body=snapshot_bytes,
        ContentType="application/json",
    )

    next_version = (
        db.scalar(
            select(Report.version)
            .where(Report.shift_id == shift.id)
            .order_by(Report.version.desc())
        )
        or 0
    ) + 1

    # Skill Registry (Reliability mission Batch B): resolves/creates the
    # immutable SkillSnapshot for this skill's current on-disk content —
    # see analysis.py's request_analysis for the fuller rationale.
    # Skill Runtime mission Phase 2: resolve the *active* snapshot, not
    # necessarily whatever is on disk right now — activation genuinely
    # controls what new jobs run.
    snapshot.skill_snapshot_id = skill_snapshot.id

    # Reliability mission Batch A: same transactional-outbox shape as
    # analysis.py's request_analysis — the ReportSnapshot above and the
    # Report row below, plus the Job + OutboxEvent enqueue_job creates,
    # all commit together at the bottom of this function. Nothing reaches
    # RabbitMQ until that commit has actually happened.
    job = enqueue_job(
        db,
        job_type="daily_report",
        requested_by=current_user.id,
        incident_id=None,
        object_refs=[
            {"bucket": settings.minio_bucket_reports, "key": snapshot_key, "sha256": snapshot_sha256}
        ],
        model=body.model,
        effort=body.effort,
        skill_name=SKILL_NAME,
        skill_version=declared_skill_version(skill_snapshot),
        skill_hash=skill_snapshot.content_hash,
        skill_execution_hash=skill_execution_hash,
        skill_snapshot_id=skill_snapshot.id,
        ai_policy=execution_policy(skill_snapshot),
    )

    report = Report(
        shift_id=shift.id,
        snapshot_id=snapshot.id,
        job_id=job.id,
        version=next_version,
        status="QUEUED",
        model=job.model,
        effort=job.effort,
        skill_name=job.skill_name,
        skill_version=job.skill_version,
        skill_hash=job.skill_hash,
        skill_snapshot_id=skill_snapshot.id,
        skill_execution_hash=skill_execution_hash,
        generated_by=current_user.id,
    )
    db.add(report)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="report.generate",
        resource_type="shift",
        resource_id=str(shift.id),
        metadata={"job_id": str(job.id), "report_version": next_version},
    )
    db.commit()
    db.refresh(job)
    db.refresh(report)
    return _to_out(report, job)


@router.get("/shifts/{shift_id}/reports", response_model=list[ReportOut])
def list_reports(
    shift_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("report.read")),
) -> list[ReportOut]:
    _get_shift_or_404(db, shift_id)

    reports = list(
        db.scalars(select(Report).where(Report.shift_id == shift_id).order_by(Report.version.desc()))
    )
    out = []
    for report in reports:
        job = db.get(Job, report.job_id)
        _sync_completed_report(db, report, job)
        out.append(_to_out(report, job))
    db.commit()
    return out


@router.get("/reports/{report_id}/download-url", response_model=ReportDownloadUrlResponse)
def get_report_download_url(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("report.download")),
) -> ReportDownloadUrlResponse:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    job = db.get(Job, report.job_id)
    _sync_completed_report(db, report, job)
    db.commit()
    if not report.report_object_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "Report is not ready to download yet")
    url = presigned_download_url(report.report_bucket, report.report_object_key)
    return ReportDownloadUrlResponse(download_url=url)


@router.get("/reports/{report_id}/download")
def download_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("report.download")),
) -> Response:
    """Streams the DOCX through the API itself instead of handing back a
    presigned MinIO URL. The presigned URL (see get_report_download_url
    above) points at minio_endpoint_url — correct for server-to-server or
    same-host use, but if the browser reaches this API through a different
    host/port than MinIO's (a forwarded/tunneled dev URL, a different
    docker network, etc.) that host is simply unreachable from the
    browser, and window.open() on it just looks like a dead link. Since
    the browser already proved it can reach this API, proxying the bytes
    through it sidesteps that whole class of environment mismatch."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    job = db.get(Job, report.job_id)
    _sync_completed_report(db, report, job)
    db.commit()
    if not report.report_object_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "Report is not ready to download yet")
    try:
        body = get_object_bytes(report.report_bucket, report.report_object_key)
    except ClientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not fetch report from storage") from exc
    filename = f"shift-report-v{report.version}.docx"
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
