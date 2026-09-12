import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.db.session import get_db
from app.deps import require_permission
from app.models.models import Shift, ShiftDefinition, User
from app.schemas.schemas import ShiftOpen, ShiftOut

router = APIRouter(prefix="/shifts", tags=["shifts"])


@router.post("/open", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
def open_shift(
    body: ShiftOpen,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("shift.operate")),
) -> ShiftOut:
    """Nothing else in this system opens a shift — there was no seed data,
    scheduler, or endpoint that ever created one, so `GET /shifts/current`
    404s forever and features gated on an active shift (report
    generation, incident.shift_id) stay permanently unusable until an
    operator opens one. Ends whatever shift is currently active first,
    since only one shift is active at a time."""
    if body.shift_definition_id is not None:
        definition = db.get(ShiftDefinition, body.shift_definition_id)
        if definition is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift definition not found")
    else:
        definition = db.scalar(
            select(ShiftDefinition).where(ShiftDefinition.enabled.is_(True)).order_by(ShiftDefinition.name)
        )
        if definition is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No enabled shift definition to open")

    current = db.scalar(select(Shift).where(Shift.state == "active"))
    if current is not None:
        current.state = "ended"
        current.ends_at = datetime.now(timezone.utc)
        current.closed_by = current_user.id

    shift = Shift(
        shift_definition_id=definition.id,
        starts_at=datetime.now(timezone.utc),
        state="active",
        opened_by=current_user.id,
    )
    db.add(shift)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="shift.open",
        resource_type="shift",
        resource_id=str(shift.id),
        metadata={"shift_definition_id": str(definition.id)},
    )
    db.commit()
    db.refresh(shift)
    return shift


@router.get("/current", response_model=ShiftOut)
def get_current_shift(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("shift.read")),
) -> ShiftOut:
    shift = db.scalar(
        select(Shift).where(Shift.state == "active").order_by(Shift.starts_at.desc())
    )
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active shift")
    return shift


@router.get("", response_model=list[ShiftOut])
def list_shifts(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("shift.read")),
) -> list[ShiftOut]:
    return list(db.scalars(select(Shift).order_by(Shift.starts_at.desc())))


@router.get("/{shift_id}", response_model=ShiftOut)
def get_shift(
    shift_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("shift.read")),
) -> ShiftOut:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    return shift


@router.post("/{shift_id}/close", response_model=ShiftOut)
def close_shift(
    shift_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("shift.operate")),
) -> ShiftOut:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")
    if shift.state != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Shift is not active")

    shift.state = "ended"
    shift.ends_at = datetime.now(timezone.utc)
    shift.closed_by = current_user.id
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="shift.close",
        resource_type="shift",
        resource_id=str(shift.id),
        metadata={},
    )
    db.commit()
    db.refresh(shift)
    return shift
