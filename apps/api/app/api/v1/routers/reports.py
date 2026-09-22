"""Daily report API with immutable snapshots and a provider-neutral runtime.

Report generation freezes incident, evidence, and analysis provenance before
dispatch. With no semantic runtime configured, new requests fail before a
job is created; historical reports remain readable and downloadable.
"""
import json
import mimetypes
import uuid

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.ai_runtime import require_ai_runtime
from app.core.config import settings
from app.core.storage import (
    get_client,
    get_object_bytes,
    head_object,
    presigned_download_url,
    sha256_of_bytes,
)
from app.db.session import get_db
from app.deps import require_permission
from app.incident_scope import shift_incident_statement
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


def _frozen_evidence_identity(evidence: Evidence) -> dict:
    """Serialize the complete byte identity of evidence into a report
    snapshot. Rendering may only resolve these frozen coordinates later."""
    return {
        "evidence_id": str(evidence.id),
        "evidence_type": evidence.evidence_type,
        "bucket": evidence.bucket,
        "object_key": evidence.object_key,
        "version_id": evidence.version_id,
        "sha256": evidence.sha256,
        "filename": evidence.original_filename,
        "content_type": evidence.mime_type,
        "byte_size": evidence.byte_size,
    }


def _get_shift_or_404(db: Session, shift_id: uuid.UUID) -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    return shift


def _lock_shift_or_404(db: Session, shift_id: uuid.UUID) -> Shift:
    """Serialize report identity allocation on the Shift row.

    The database unique constraint remains the final guard, while this
    short-lived row lock makes concurrent MAX(version)+1 allocation
    deterministic without introducing an external lock manager.
    """
    shift = db.scalar(select(Shift).where(Shift.id == shift_id).with_for_update())
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    return shift


def _allocate_report_version(db: Session, shift: Shift) -> int:
    """Return the next version while the caller holds the Shift row lock."""
    return (
        db.scalar(
            select(Report.version)
            .where(Report.shift_id == shift.id)
            .order_by(Report.version.desc())
        )
        or 0
    ) + 1


def _build_snapshot(db: Session, shift: Shift, report_skill_snapshot) -> dict:
    """Real query over this shift's incidents and their current analysis
    run, frozen into one JSON document — this is what the skill actually
    sees, and what a completed Report's content is judged against later,
    however the underlying incidents change afterward."""
    report_manifest = load_manifest(report_skill_snapshot)
    shift_display_name = getattr(shift.definition, "name", None)
    shift_timezone = getattr(shift.definition, "timezone", None) or "UTC"
    incidents = list(
        db.scalars(shift_incident_statement(shift.id))
    )
    incident_rows = []
    for incident in incidents:
        current_runs = list(db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.incident_id == incident.id, AnalysisRun.current.is_(True))
            .limit(2)
        ))
        if len(current_runs) > 1:
            raise RuntimeError(f"incident {incident.id} has multiple current AnalysisRuns")
        run = current_runs[0] if current_runs else None
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
            db.scalars(
                select(Evidence).where(
                    Evidence.incident_id == incident.id,
                    Evidence.lifecycle_state == "ACTIVE",
                )
            )
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
                "log_evidence": _frozen_evidence_identity(log_evidence) if log_evidence else None,
                "screenshots": [
                    _frozen_evidence_identity(e)
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
        # ShiftDefinition is mutable, so freeze the configured display value
        # into the immutable snapshot rather than resolving it while polling
        # or rendering the report later.
        "shift_name": shift_display_name,
        "shift_code": shift_display_name,
        "shift_display_name": shift_display_name,
        # Freeze the operational calendar with the UTC boundaries. Historical
        # reports must not change if the mutable ShiftDefinition is edited.
        "shift_timezone": shift_timezone,
        "report_skill_snapshot_id": str(report_skill_snapshot.id),
        "report_skill_execution_hash": compute_execution_hash(db, report_skill_snapshot),
        # Coverage is part of the immutable report-skill contract. The
        # runtime worker must validate against this frozen policy, never the current
        # checkout or a later activation.
        "composition_profile": report_manifest.get("composition_profile"),
        "coverage": report_manifest.get("coverage") or {},
        "incidents": incident_rows,
    }


def _report_object_key(job_id: uuid.UUID) -> str:
    return f"reports/{job_id}/report.docx"


def _document_object_key(job_id: uuid.UUID) -> str:
    return f"reports/{job_id}/document.json"


def _document_screenshots_object_key(job_id: uuid.UUID) -> str:
    return f"reports/{job_id}/document.screenshots.json"


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
        downloadable=bool(report.report_object_key and report.report_version_id),
        previewable=bool(report.document_object_key and report.document_version_id),
        report_version_id=report.report_version_id,
    )


def _sync_completed_report(db: Session, report: Report, job: Job) -> None:
    """Sync a completed report's immutable DOCX artifact once,
    but the artifact here is a DOCX in noc-reports (not a JSON result in
    noc-job-artifacts) — the runtime worker writes it to a deterministic key this
    function re-derives rather than being told."""
    if job.status != "COMPLETED":
        return
    # All artifact identities are write-once. The runtime worker records preview
    # versions before marking the Job complete; once the DOCX has been
    # pinned, never HEAD the deterministic key again or a later overwrite
    # could silently change this historical Report row.
    if report.report_version_id:
        return

    key = report.report_object_key or _report_object_key(job.id)
    bucket = report.report_bucket or settings.minio_bucket_reports
    try:
        metadata = head_object(bucket, key)
        version_id = metadata.get("VersionId")
        body = get_object_bytes(
            bucket,
            key,
            version_id=version_id,
        )
    except ClientError:
        # The runtime worker marked the Job COMPLETED but the DOCX isn't visible yet
        # (or was never written) — leave the report unfilled, try again
        # next poll, never fabricate a download.
        return

    report.report_bucket = bucket
    report.report_object_key = key
    report.report_version_id = version_id
    report.report_sha256 = sha256_of_bytes(body)
    report.report_byte_size = len(body)
    report.report_content_type = metadata.get("ContentType") or mimetypes.guess_type(key)[0]
    report.generated_at = job.completed_at
    if report.model is None:
        report.model = job.model
    if report.effort is None:
        report.effort = job.effort
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
    shift = _lock_shift_or_404(db, shift_id)

    # Do not freeze, upload, or enqueue anything while the semantic runtime
    # is absent. This keeps the outbox free of unserviceable work.
    require_ai_runtime()
    if not settings.daily_report_ai_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DAILY_REPORT_RUNTIME_UNAVAILABLE",
                "message": "Daily Alert Report AI generation is not enabled yet; log analysis must pass its quality gate first.",
            },
        )

    next_version = _allocate_report_version(db, shift)

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
    # runtime support can download+checksum-verify it through the
    # same object_refs path Milestone 13 already built — no separate
    # "small JSON inline in the queue message" code path to maintain.
    snapshot_key = f"snapshots/{shift.id}/{snapshot.id}.json"
    get_client().put_object(
        Bucket=settings.minio_bucket_reports,
        Key=snapshot_key,
        Body=snapshot_bytes,
        ContentType="application/json",
    )

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
        model=None,
        effort=None,
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
    if not report.report_version_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Report artifact is not version-pinned yet")
    url = presigned_download_url(
        report.report_bucket,
        report.report_object_key,
        version_id=report.report_version_id,
    )
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
    if not report.report_version_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Report artifact is not version-pinned yet")
    try:
        body = get_object_bytes(
            report.report_bucket,
            report.report_object_key,
            version_id=report.report_version_id,
        )
    except ClientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not fetch report from storage") from exc
    if report.report_sha256 and sha256_of_bytes(body) != report.report_sha256:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Report artifact checksum mismatch")
    filename = f"shift-report-v{report.version}.docx"
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _report_or_404(db: Session, report_id: uuid.UUID) -> tuple[Report, Job]:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    job = db.get(Job, report.job_id)
    _sync_completed_report(db, report, job)
    db.commit()
    if not report.report_object_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "Report is not ready yet")
    return report, job


@router.get("/reports/{report_id}/document")
def get_report_document(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("report.read")),
) -> Response:
    """The web Report Builder preview's data source: the exact same
    composed `ReportDocument` the DOCX was rendered from (see
    report document adapter), as browser-safe JSON —
    screenshots are referenced by index only, never by MinIO bucket/key.
    Not every report was produced by the report-document-v1 renderer
    profile (older frozen snapshots may have used the legacy assembler),
    so a missing document.json is a normal 404, not an error."""
    report, _job = _report_or_404(db, report_id)
    if not report.document_version_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No structured preview is available for this report")
    try:
        body = get_object_bytes(
            report.report_bucket,
            report.document_object_key or _document_object_key(report.job_id),
            version_id=report.document_version_id,
        )
    except ClientError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No structured preview is available for this report"
        )
    if report.document_sha256 and sha256_of_bytes(body) != report.document_sha256:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Report preview checksum mismatch")
    return Response(content=body, media_type=report.document_content_type or "application/json")


@router.get("/reports/{report_id}/document/screenshots/{index}")
def get_report_document_screenshot(
    report_id: uuid.UUID,
    index: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("report.read")),
) -> Response:
    """Proxies one screenshot referenced by the preview payload's
    `{"type": "screenshot", "index": N}` blocks. The real bucket/object_key
    for each index lives only in the private document.screenshots.json
    artifact this reads server-side — it is never sent to the browser."""
    report, _job = _report_or_404(db, report_id)
    if not report.screenshots_version_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No structured preview is available for this report")
    try:
        raw_index = get_object_bytes(
            report.report_bucket,
            report.screenshots_object_key or _document_screenshots_object_key(report.job_id),
            version_id=report.screenshots_version_id,
        )
    except ClientError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No structured preview is available for this report"
        )
    screenshots = json.loads(raw_index)
    if report.screenshots_sha256 and sha256_of_bytes(raw_index) != report.screenshots_sha256:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Report screenshot index checksum mismatch")
    if index < 0 or index >= len(screenshots):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such screenshot")
    entry = screenshots[index]
    try:
        image_bytes = get_object_bytes(
            entry["bucket"], entry["object_key"], version_id=entry.get("version_id")
        )
    except ClientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not fetch screenshot from storage") from exc
    expected_sha256 = entry.get("sha256")
    if expected_sha256 and sha256_of_bytes(image_bytes) != expected_sha256:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Screenshot integrity check failed")
    content_type = mimetypes.guess_type(entry.get("filename") or "")[0] or "application/octet-stream"
    return Response(content=image_bytes, media_type=content_type)
