"""Milestone 15 (Search / Knowledge, master plan §32): PostgreSQL
full-text + trigram search over incidents, replacing the client-side
substring filter `apps/web`'s KnowledgeBase.tsx used until now.

V1 scope per §32/§16: index title, service, environment, notes,
extracted OCR text (OcrRun.raw_text via each incident's evidence), and
analysis text (AnalysisRun.result_text). Exception/logger names and tags
aren't modeled anywhere yet (no incident has a parsed exception/logger
field, no tags table exists) — per coding-agent rule 20 ("build only
what the current milestone needs"), those two are left out of the index
rather than faked, same precedent as `incident_relationships` staying
unmodeled since Milestone 13. "Report links" (§16's result-card spec)
are deferred too — deliberately, see the Milestone 15 implementation log.

Semantic search (pgvector) is explicitly out of scope for V1 per §32.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_permission
from app.models.models import AnalysisRun, Evidence, Incident, OcrRun, User
from app.schemas.schemas import SearchResponse, SearchResultOut

router = APIRouter(tags=["search"])


def _incident_tsvector():
    return func.to_tsvector(
        "english",
        func.coalesce(Incident.title, "")
        + " "
        + func.coalesce(Incident.service, "")
        + " "
        + func.coalesce(Incident.environment, "")
        + " "
        + func.coalesce(Incident.notes, ""),
    )


@router.get("/search", response_model=SearchResponse)
def search(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("knowledge.read")),
    q: str | None = Query(default=None),
    service: str | None = None,
    environment: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    has_log: bool | None = None,
    shift_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=25, le=200),
    offset: int = Query(default=0, ge=0),
) -> SearchResponse:
    stmt = select(Incident)

    if service:
        stmt = stmt.where(Incident.service == service)
    if environment:
        stmt = stmt.where(Incident.environment == environment)
    if status_filter:
        stmt = stmt.where(Incident.status == status_filter)
    if shift_id:
        stmt = stmt.where(Incident.shift_id == shift_id)
    if date_from:
        stmt = stmt.where(Incident.triggered_at >= date_from)
    if date_to:
        stmt = stmt.where(Incident.triggered_at <= date_to)

    log_exists = exists().where(
        and_(Evidence.incident_id == Incident.id, Evidence.evidence_type == "LOG", Evidence.lifecycle_state == "ACTIVE")
    )
    if has_log is True:
        stmt = stmt.where(log_exists)
    elif has_log is False:
        stmt = stmt.where(~log_exists)

    rank_col = None
    if q:
        tsquery = func.websearch_to_tsquery("english", q)
        tsvector = _incident_tsvector()

        ocr_match = exists().where(
            and_(
                Evidence.incident_id == Incident.id,
                Evidence.id == OcrRun.evidence_id,
                Evidence.lifecycle_state == "ACTIVE",
                OcrRun.raw_text.isnot(None),
                OcrRun.raw_text.ilike(f"%{q}%"),
            )
        )
        analysis_match = exists().where(
            and_(
                AnalysisRun.incident_id == Incident.id,
                AnalysisRun.result_text.isnot(None),
                AnalysisRun.result_text.ilike(f"%{q}%"),
            )
        )

        stmt = stmt.where(
            or_(
                tsvector.op("@@")(tsquery),
                Incident.title.op("%")(q),  # pg_trgm fuzzy match, uses incidents_title_trgm_idx
                ocr_match,
                analysis_match,
            )
        )
        rank_col = func.ts_rank(tsvector, tsquery)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    if rank_col is not None:
        stmt = stmt.order_by(rank_col.desc(), Incident.triggered_at.desc())
    else:
        stmt = stmt.order_by(Incident.triggered_at.desc())

    incidents = list(db.scalars(stmt.limit(limit).offset(offset)))

    items: list[SearchResultOut] = []
    for incident in incidents:
        has_log_value = db.scalar(
        select(exists().where(and_(Evidence.incident_id == incident.id, Evidence.evidence_type == "LOG", Evidence.lifecycle_state == "ACTIVE")))
        )
        current_run = db.scalar(
            select(AnalysisRun).where(AnalysisRun.incident_id == incident.id, AnalysisRun.current.is_(True))
        )
        summary = None
        if current_run and current_run.result_json:
            summary = current_run.result_json.get("summary")
        recurrence_count = (
            db.scalar(
                select(func.count())
                .select_from(Incident)
                .where(func.lower(Incident.title) == incident.title.lower(), Incident.id != incident.id)
            )
            or 0
        )
        items.append(
            SearchResultOut(
                id=incident.id,
                display_id=incident.display_id,
                title=incident.title,
                service=incident.service,
                environment=incident.environment,
                status=incident.status,
                triggered_at=incident.triggered_at,
                has_log=bool(has_log_value),
                analysis_summary=summary,
                recurrence_count=recurrence_count,
            )
        )

    return SearchResponse(items=items, total=total)
