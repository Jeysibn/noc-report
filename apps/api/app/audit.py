import uuid

from sqlalchemy.orm import Session

from app.models.models import AuditLog


def record_audit(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict | None = None,
) -> None:
    """Append-only audit trail (master plan §22.13) — every mutating admin
    or incident action funnels through here rather than each router writing
    its own audit row shape."""
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata or {},
        )
    )
