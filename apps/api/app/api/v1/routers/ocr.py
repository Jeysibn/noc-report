import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.ocr import run_ocr
from app.core.storage import get_object_bytes
from app.db.session import get_db
from app.deps import require_permission
from app.models.models import Evidence, OcrRun, User
from app.schemas.schemas import OcrRunOut

router = APIRouter(tags=["ocr"])


@router.post("/evidence/{evidence_id}/ocr", response_model=OcrRunOut, status_code=status.HTTP_201_CREATED)
def run_ocr_on_evidence(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> OcrRunOut:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")

    ocr_run = OcrRun(evidence_id=evidence_id, engine="paddleocr", status="PROCESSING")
    db.add(ocr_run)
    db.flush()

    try:
        image_bytes = get_object_bytes(evidence.bucket, evidence.object_key)
        result = run_ocr(image_bytes)
    except Exception as exc:  # noqa: BLE001 - any engine/storage failure marks the run FAILED
        ocr_run.status = "FAILED"
        ocr_run.extracted_json = {"error": str(exc)}
        ocr_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(ocr_run)
        return ocr_run

    ocr_run.status = "REVIEW_REQUIRED"
    ocr_run.raw_text = result.raw_text
    ocr_run.extracted_json = {
        "fields": result.fields,
        "lines": [
            {"text": ln.text, "confidence": ln.confidence, "bbox": ln.bbox}
            for ln in result.lines
        ],
    }
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
