import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.audit import record_audit
from app.core.config import settings
from app.core.ocr import run_ocr
from app.core.storage import get_client, get_object_bytes, head_object, sha256_of_bytes
from app.db.session import get_db
from app.deps import require_permission
from app.incident_prefill import build_incident_prefill, configured_local_inference, normalize_ocr_text
from app.models.models import Evidence, Incident, IncidentPrefillRun, OcrRun, User
from app.schemas.schemas import EvidenceOut, IncidentPrefillOut, OcrRunOut

router = APIRouter(tags=["ocr"])


def _engine_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("paddleocr")
    except Exception:  # pragma: no cover - dependency metadata varies by image
        return None


def _extraction_payload(result) -> dict:
    return {
        "fields": result.fields,
        "lines": [
            {"text": ln.text, "confidence": ln.confidence, "bbox": ln.bbox}
            for ln in result.lines
        ],
    }


@router.post("/evidence/{evidence_id}/ocr", response_model=OcrRunOut, status_code=status.HTTP_201_CREATED)
def run_ocr_on_evidence(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> OcrRunOut:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    if evidence.lifecycle_state != "ACTIVE":
        raise HTTPException(status.HTTP_410_GONE, "Evidence is no longer available")

    ocr_started = time.perf_counter()
    ocr_run = OcrRun(evidence_id=evidence_id, engine="paddleocr", status="PROCESSING")
    db.add(ocr_run)
    db.flush()

    try:
        image_bytes = get_object_bytes(
            evidence.bucket,
            evidence.object_key,
            version_id=evidence.version_id,
        )
        result = run_ocr(image_bytes)
    except Exception as exc:  # noqa: BLE001 - any engine/storage failure marks the run FAILED
        ocr_run.status = "FAILED"
        ocr_run.extracted_json = {"error": str(exc)}
        ocr_run.ocr_duration_ms = round((time.perf_counter() - ocr_started) * 1000)
        ocr_run.engine_version = _engine_version()
        ocr_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(ocr_run)
        return ocr_run

    ocr_run.status = "REVIEW_REQUIRED"
    ocr_run.raw_text = result.raw_text
    ocr_run.normalized_text = normalize_ocr_text(result.raw_text)
    ocr_run.engine_version = _engine_version()
    ocr_run.extracted_json = _extraction_payload(result)
    ocr_run.ocr_duration_ms = round((time.perf_counter() - ocr_started) * 1000)
    ocr_run.completed_at = datetime.now(timezone.utc)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="evidence.ocr",
        resource_type="ocr_run",
        resource_id=str(ocr_run.id),
        metadata={"evidence_id": str(evidence_id), "status": ocr_run.status},
    )
    db.commit()
    db.refresh(ocr_run)
    return ocr_run


@router.post("/ocr/prefill", response_model=IncidentPrefillOut, status_code=status.HTTP_201_CREATED)
def create_incident_prefill(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> IncidentPrefillOut:
    """Persist a draft screenshot, run OCR, then produce review-only fields.

    This endpoint intentionally creates no Incident. The source object is
    attached later by the operator-confirmed Incident workflow.
    """
    content = file.file.read(settings.max_evidence_upload_bytes + 1)
    if len(content) > settings.max_evidence_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Screenshot exceeds the upload limit")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Screenshot is empty")

    prefill_id = uuid.uuid4()
    filename = os.path.basename(file.filename or "screenshot.png")
    key = f"prefills/{prefill_id}/{filename}"
    get_client().put_object(
        Bucket=settings.minio_bucket_evidence,
        Key=key,
        Body=content,
        ContentType=file.content_type or "application/octet-stream",
    )
    run = IncidentPrefillRun(
        id=prefill_id,
        source_bucket=settings.minio_bucket_evidence,
        source_object_key=key,
        source_filename=filename,
        source_mime_type=file.content_type,
        source_sha256=sha256_of_bytes(content),
        ocr_engine="paddleocr",
        ocr_engine_version=_engine_version(),
        ocr_extraction_json={},
        status="PROCESSING",
        created_by=current_user.id,
    )
    db.add(run)
    db.flush()
    ocr_started = time.perf_counter()
    try:
        result = run_ocr(content)
        extraction = _extraction_payload(result)
        run.raw_ocr_text = result.raw_text
        run.normalized_ocr_text = normalize_ocr_text(result.raw_text)
        run.ocr_extraction_json = extraction
        run.ocr_duration_ms = round((time.perf_counter() - ocr_started) * 1000)
        prefill_started = time.perf_counter()
        prefill = build_incident_prefill(
            {"raw_text": result.raw_text, "lines": extraction["lines"]},
            inference=configured_local_inference(),
            known_services=settings.known_prefill_services,
        )
        run.prefill_json = prefill.model_dump(mode="json")
        run.prefill_status = prefill.mapper_status
        run.prefill_model = prefill.mapper_model
        run.prefill_duration_ms = round((time.perf_counter() - prefill_started) * 1000)
        run.status = "FALLBACK" if prefill.mapper_status == "FALLBACK" else "REVIEW_REQUIRED"
        run.error_message = prefill.mapper_error
    except Exception as exc:  # OCR failure must remain distinct from mapper failure
        run.status = "OCR_FAILED"
        run.error_message = str(exc)[:1000]
        run.ocr_duration_ms = round((time.perf_counter() - ocr_started) * 1000)
    run.completed_at = datetime.now(timezone.utc)
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.prefill",
        resource_type="incident_prefill_run",
        resource_id=str(run.id),
        metadata={"status": run.status, "source_filename": filename},
    )
    db.commit()
    db.refresh(run)
    return run


@router.post(
    "/ocr/prefills/{prefill_id}/attach/{incident_id}",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
)
def attach_prefill_to_incident(
    prefill_id: uuid.UUID,
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> EvidenceOut:
    prefill = db.get(IncidentPrefillRun, prefill_id)
    if prefill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident prefill not found")
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    if prefill.status == "OCR_FAILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot attach a prefill whose OCR failed")
    if prefill.incident_id not in (None, incident_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Prefill is already attached to another incident")

    existing = db.scalar(
        select(Evidence).where(
            Evidence.incident_id == incident_id,
            Evidence.object_key == prefill.source_object_key,
        )
    )
    if existing is not None:
        return existing
    try:
        head = head_object(prefill.source_bucket, prefill.source_object_key)
    except Exception as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Prefill screenshot is no longer available") from exc
    evidence = Evidence(
        incident_id=incident_id,
        evidence_type="ALERT_SCREENSHOT",
        bucket=prefill.source_bucket,
        object_key=prefill.source_object_key,
        original_filename=prefill.source_filename,
        mime_type=prefill.source_mime_type,
        byte_size=head.get("ContentLength"),
        sha256=prefill.source_sha256,
        version_id=head.get("VersionId"),
        uploaded_by=current_user.id,
    )
    db.add(evidence)
    prefill.incident_id = incident_id
    db.flush()
    suggested = {
        field: (prefill.prefill_json or {}).get(field, {}).get("value")
        for field in ("title", "service", "environment", "status", "trigger_value", "notes")
    }
    final = {
        "title": incident.title,
        "service": incident.service,
        "environment": incident.environment,
        "status": incident.status,
        "trigger_value": incident.trigger_value,
        "notes": incident.notes,
    }
    corrections = {
        field: {"suggested": suggested[field], "final": final[field]}
        for field in suggested
        if suggested[field] != final[field]
    }
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.prefill.attached",
        resource_type="evidence",
        resource_id=str(evidence.id),
        metadata={
            "prefill_id": str(prefill_id),
            "incident_id": str(incident_id),
            "corrections": corrections,
        },
    )
    db.commit()
    db.refresh(evidence)
    return evidence


@router.get("/ocr-runs/{ocr_run_id}", response_model=OcrRunOut)
def get_ocr_run(
    ocr_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> OcrRunOut:
    ocr_run = db.get(OcrRun, ocr_run_id)
    if ocr_run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OCR run not found")
    return ocr_run
