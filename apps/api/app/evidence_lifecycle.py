"""Authoritative Evidence lifecycle and storage cleanup seam.

Database state is committed before MinIO is mutated. A purge can therefore
fail safely and be retried: the row is a non-visible ``PURGE_PENDING``
tombstone rather than a live record whose bytes disappeared underneath it.
Historical report snapshots and execution records protect evidence from
purge because report reproducibility has priority over storage reclamation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import delete_object
from app.models.models import AnalysisRun, Evidence, OcrRun, ReportSnapshot


class EvidenceReferencedError(RuntimeError):
    def __init__(self, references: tuple[str, ...]):
        self.references = references
        super().__init__("evidence is retained by: " + ", ".join(references))


@dataclass(frozen=True)
class EvidencePurgePlan:
    evidence_id: uuid.UUID
    bucket: str
    object_key: str
    version_id: str | None


def _contains_evidence_id(value, evidence_id: str) -> bool:
    if isinstance(value, dict):
        if str(value.get("evidence_id")) == evidence_id:
            return True
        return any(_contains_evidence_id(item, evidence_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_evidence_id(item, evidence_id) for item in value)
    return False


def references_for(db: Session, evidence_id: uuid.UUID) -> tuple[str, ...]:
    references: list[str] = []
    if db.scalar(select(OcrRun.id).where(OcrRun.evidence_id == evidence_id).limit(1)):
        references.append("OCR")
    if db.scalar(select(AnalysisRun.id).where(AnalysisRun.log_evidence_id == evidence_id).limit(1)):
        references.append("analysis")
    for snapshot in db.scalars(select(ReportSnapshot)):
        if _contains_evidence_id(snapshot.snapshot_json, str(evidence_id)):
            references.append("historical report snapshot")
            break
    return tuple(references)


def request_purge(db: Session, evidence: Evidence) -> EvidencePurgePlan:
    if evidence.lifecycle_state == "PURGED":
        return EvidencePurgePlan(evidence.id, evidence.bucket, evidence.object_key, evidence.version_id)
    references = references_for(db, evidence.id)
    if references:
        raise EvidenceReferencedError(references)
    evidence.lifecycle_state = "PURGE_PENDING"
    evidence.deleted_at = evidence.deleted_at or datetime.now(timezone.utc)
    db.flush()
    return EvidencePurgePlan(evidence.id, evidence.bucket, evidence.object_key, evidence.version_id)


def purge_storage(plan: EvidencePurgePlan) -> bool:
    """Delete the exact immutable object. Missing is an idempotent success."""
    try:
        delete_object(plan.bucket, plan.object_key, version_id=plan.version_id)
    except ClientError as exc:
        code = str((exc.response or {}).get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
            return True
        raise
    return True


def mark_purged(db: Session, evidence_id: uuid.UUID) -> None:
    evidence = db.get(Evidence, evidence_id)
    if evidence is not None:
        evidence.lifecycle_state = "PURGED"
        db.commit()
