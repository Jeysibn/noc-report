import uuid
from datetime import datetime, timedelta, timezone

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
    sha256_of_object,
)
from app.db.session import get_db
from app.deps import require_permission
from app.evidence_lifecycle import EvidenceReferencedError, mark_purged, purge_storage, request_purge
from app.models.models import Evidence, EvidenceUploadIntent, Incident, User
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
    current_user: User = Depends(require_permission("incident.evidence.upload")),
) -> EvidenceUploadUrlResponse:
    _get_incident_or_404(db, incident_id)
    if body.expected_byte_size is not None and body.expected_byte_size > settings.max_evidence_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File exceeds the configured upload limit")
    upload_id = uuid.uuid4()
    bucket = settings.minio_bucket_evidence
    key = evidence_object_key(str(incident_id), body.evidence_type, body.filename, upload_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.evidence_upload_intent_ttl_seconds)
    intent = EvidenceUploadIntent(
        id=upload_id,
        incident_id=incident_id,
        created_by=current_user.id,
        bucket=bucket,
        object_key=key,
        evidence_type=body.evidence_type,
        original_filename=body.filename,
        expected_content_type=body.content_type,
        expected_byte_size=body.expected_byte_size,
        expires_at=expires_at,
    )
    db.add(intent)
    db.commit()
    url = presigned_upload_url(bucket, key, content_type=body.content_type)
    return EvidenceUploadUrlResponse(upload_id=upload_id, upload_url=url, expires_at=expires_at)


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
    intent = db.scalar(
        select(EvidenceUploadIntent)
        .where(EvidenceUploadIntent.id == body.upload_id)
        .with_for_update()
    )
    if intent is None or intent.incident_id != incident_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload intent not found")
    if intent.created_by != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Upload intent belongs to another user")
    if intent.state == "COMPLETED" and intent.evidence_id:
        existing = db.get(Evidence, intent.evidence_id)
        if existing is not None:
            return existing
    if intent.state != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Upload intent is {intent.state.lower()}")
    now = datetime.now(timezone.utc)
    if intent.expires_at <= now:
        intent.state = "EXPIRED"
        db.commit()
        raise HTTPException(status.HTTP_410_GONE, "Upload intent has expired")
    try:
        head = head_object(intent.bucket, intent.object_key)
    except ClientError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Object not found in storage — upload must complete before calling this endpoint",
        )

    byte_size = head.get("ContentLength") or 0
    if intent.expected_byte_size is not None and byte_size != intent.expected_byte_size:
        delete_object(intent.bucket, intent.object_key, version_id=head.get("VersionId"))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded object size does not match the upload intent")
    actual_content_type = head.get("ContentType")
    if intent.expected_content_type and actual_content_type != intent.expected_content_type:
        delete_object(intent.bucket, intent.object_key, version_id=head.get("VersionId"))
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Uploaded object MIME type does not match the upload intent")
    if byte_size > settings.max_evidence_upload_bytes:
        # The object already landed in MinIO (a presigned PUT can't cap
        # size up front) — delete it rather than leave an orphaned,
        # over-limit object with no Evidence row pointing at it.
        delete_object(intent.bucket, intent.object_key, version_id=head.get("VersionId"))
        limit_mb = settings.max_evidence_upload_bytes // (1024 * 1024)
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File exceeds the {limit_mb} MB upload limit",
        )

    version_id = head.get("VersionId")
    sha256 = sha256_of_object(intent.bucket, intent.object_key, version_id=version_id)
    record = Evidence(
        incident_id=incident_id,
        evidence_type=intent.evidence_type,
        bucket=intent.bucket,
        object_key=intent.object_key,
        original_filename=intent.original_filename,
        mime_type=actual_content_type or intent.expected_content_type,
        byte_size=byte_size,
        sha256=sha256,
        version_id=version_id,
        uploaded_by=current_user.id,
    )
    db.add(record)
    db.flush()
    intent.state = "COMPLETED"
    intent.evidence_id = record.id
    intent.completed_at = now
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.evidence.upload",
        resource_type="evidence",
        resource_id=str(record.id),
        metadata={"incident_id": str(incident_id), "evidence_type": intent.evidence_type},
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
            .where(Evidence.incident_id == incident_id, Evidence.lifecycle_state == "ACTIVE")
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
    if record.lifecycle_state != "ACTIVE":
        raise HTTPException(status.HTTP_410_GONE, "Evidence is no longer available")
    url = presigned_download_url(record.bucket, record.object_key, version_id=record.version_id)
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

    try:
        plan = request_purge(db, record)
    except EvidenceReferencedError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="evidence.delete",
        resource_type="evidence",
        resource_id=str(evidence_id),
        metadata={"incident_id": str(record.incident_id)},
    )
    db.commit()
    try:
        purge_storage(plan)
    except ClientError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Evidence cleanup is pending retry") from exc
    mark_purged(db, evidence_id)
