import uuid

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.config import settings
from app.core.storage import (
    delete_object,
    evidence_object_key,
    head_object,
    presigned_download_url,
    presigned_upload_url,
)
from app.db.session import get_db
from app.deps import require_permission
from app.models.models import Evidence, Incident, User
from app.schemas.schemas import (
    EvidenceCompleteRequest,
    EvidenceDownloadUrlResponse,
    EvidenceOut,
    EvidenceUploadUrlRequest,
    EvidenceUploadUrlResponse,
)

router = APIRouter(tags=["evidence"])


def _get_incident_or_404(db: Session, incident_id: uuid.UUID) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


@router.post(
    "/incidents/{incident_id}/evidence/upload-url",
    response_model=EvidenceUploadUrlResponse,
)
def request_upload_url(
    incident_id: uuid.UUID,
    body: EvidenceUploadUrlRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.evidence.upload")),
) -> EvidenceUploadUrlResponse:
    _get_incident_or_404(db, incident_id)
    bucket = settings.minio_bucket_evidence
    key = evidence_object_key(str(incident_id), body.evidence_type, body.filename)
    url = presigned_upload_url(bucket, key, content_type=body.content_type)
    return EvidenceUploadUrlResponse(upload_url=url, bucket=bucket, object_key=key)


@router.post(
    "/incidents/{incident_id}/evidence/complete",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
)
def complete_upload(
    incident_id: uuid.UUID,
    body: EvidenceCompleteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> EvidenceOut:
    _get_incident_or_404(db, incident_id)

    try:
        head = head_object(body.bucket, body.object_key)
    except ClientError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Object not found in storage — upload must complete before calling this endpoint",
        )

    byte_size = head.get("ContentLength") or 0
    if byte_size > settings.max_evidence_upload_bytes:
        # The object already landed in MinIO (a presigned PUT can't cap
        # size up front) — delete it rather than leave an orphaned,
        # over-limit object with no Evidence row pointing at it.
        delete_object(body.bucket, body.object_key)
        limit_mb = settings.max_evidence_upload_bytes // (1024 * 1024)
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File exceeds the {limit_mb} MB upload limit",
        )

    record = Evidence(
        incident_id=incident_id,
        evidence_type=body.evidence_type,
        bucket=body.bucket,
        object_key=body.object_key,
        original_filename=body.original_filename,
        mime_type=body.mime_type,
        byte_size=head.get("ContentLength"),
        sha256=body.sha256,
        version_id=head.get("VersionId"),
        uploaded_by=current_user.id,
    )
    db.add(record)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.evidence.upload",
        resource_type="evidence",
        resource_id=str(record.id),
        metadata={"incident_id": str(incident_id), "evidence_type": body.evidence_type},
    )
    db.commit()
    db.refresh(record)
    return record


@router.get("/incidents/{incident_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> list[EvidenceOut]:
    _get_incident_or_404(db, incident_id)
    return list(
        db.scalars(
            select(Evidence)
            .where(Evidence.incident_id == incident_id)
            .order_by(Evidence.created_at)
        )
    )


@router.get("/evidence/{evidence_id}/download-url", response_model=EvidenceDownloadUrlResponse)
def get_download_url(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> EvidenceDownloadUrlResponse:
    record = db.get(Evidence, evidence_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    url = presigned_download_url(record.bucket, record.object_key)
    return EvidenceDownloadUrlResponse(download_url=url)


@router.delete("/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> None:
    record = db.get(Evidence, evidence_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")

    # Master plan §11: uploaded evidence is immutable; delete removes the
    # object from storage (no soft-delete flag defined at Milestone 9 scope)
    # but the audit trail keeps a permanent record that it existed.
    try:
        delete_object(record.bucket, record.object_key)
    except ClientError:
        pass

    db.delete(record)
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="evidence.delete",
        resource_type="evidence",
        resource_id=str(evidence_id),
        metadata={"incident_id": str(record.incident_id)},
    )
    db.commit()
