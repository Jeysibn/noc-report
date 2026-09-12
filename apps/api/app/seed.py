"""
Bootstrap script: creates the three master-plan roles (§23) with their
permission sets, and one initial Admin user, if they don't already exist.
Idempotent — safe to run multiple times (e.g. on every container start).

Usage: python -m app.seed
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

import uuid

from app.core.security import hash_password
from app.db.session import Base, SessionLocal, engine
from app.models.models import Permission, Role, ShiftDefinition, SystemConfig, User

# Milestone 17 gap follow-up (AI Configuration): system_config is a
# single-row table — this fixed id is how `seed()`/the admin endpoint
# both locate that one row without a separate "is this the row" flag.
SYSTEM_CONFIG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Milestone 17 gap follow-up (Shift Configuration): the three shifts named
# in the master plan's Dashboard mock (§9.2) and the old Admin.tsx mock
# display — Day/Swing/Night, Asia/Manila, back-to-back with no gaps. There
# was previously no seed data for shift_definitions at all; Shift rows in
# earlier milestones were created against manually-inserted definitions.
DEFAULT_SHIFT_DEFINITIONS = [
    {"name": "Day", "start_time": "08:00:00", "end_time": "16:00:00"},
    {"name": "Swing", "start_time": "16:00:00", "end_time": "00:00:00"},
    {"name": "Night", "start_time": "00:00:00", "end_time": "08:00:00"},
]

# master plan §23 permission model
ALL_PERMISSIONS = [
    "dashboard.read",
    "incident.read",
    "incident.create",
    "incident.update",
    "incident.delete",
    "incident.evidence.upload",
    "incident.analysis.execute",
    "report.read",
    "report.generate",
    "report.download",
    "knowledge.read",
    "analytics.read",
    "user.read",
    "user.manage",
    "role.manage",
    "shift.read",
    "shift.operate",
    "shift.manage",
    "system.read",
    "system.configure",
    "audit.read",
]

NOC_PERMISSIONS = [
    "dashboard.read",
    "incident.read",
    "incident.create",
    "incident.update",
    "incident.evidence.upload",
    "incident.analysis.execute",
    "report.read",
    "report.generate",
    "report.download",
    "knowledge.read",
    "shift.read",
    "shift.operate",
]

DEVOPS_PERMISSIONS = NOC_PERMISSIONS + ["analytics.read", "audit.read"]

ROLE_PERMISSIONS = {
    "NOC": NOC_PERMISSIONS,
    "DevOps": DEVOPS_PERMISSIONS,
    "Admin": ALL_PERMISSIONS,
}


def _get_or_create_permission(db: Session, name: str) -> Permission:
    perm = db.scalar(select(Permission).where(Permission.name == name))
    if perm is None:
        perm = Permission(name=name)
        db.add(perm)
        db.flush()
    return perm


def _get_or_create_role(db: Session, name: str, permission_names: list[str]) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(name=name)
        db.add(role)
        db.flush()
    role.permissions = [_get_or_create_permission(db, p) for p in permission_names]
    return role


def seed(db: Session) -> None:
    roles_by_name = {
        name: _get_or_create_role(db, name, perms) for name, perms in ROLE_PERMISSIONS.items()
    }
    db.commit()

    admin_user = db.scalar(select(User).where(User.username == "admin"))
    if admin_user is None:
        admin_user = User(
            username="admin",
            display_name="Administrator",
            email="admin@example.com",
            password_hash=hash_password("ChangeMe123!"),
            roles=[roles_by_name["Admin"]],
        )
        db.add(admin_user)
        db.commit()
        print("Created initial admin user: username='admin' password='ChangeMe123!' "
              "(change this immediately in a real deployment)")
    else:
        print("Admin user already exists — skipped")

    for defn in DEFAULT_SHIFT_DEFINITIONS:
        existing = db.scalar(
            select(ShiftDefinition).where(ShiftDefinition.name == defn["name"])
        )
        if existing is None:
            db.add(ShiftDefinition(timezone="Asia/Manila", enabled=True, **defn))
    db.commit()

    if db.get(SystemConfig, SYSTEM_CONFIG_ID) is None:
        db.add(SystemConfig(id=SYSTEM_CONFIG_ID))
        db.commit()


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)  # no-op once Alembic migrations are current
    with SessionLocal() as db:
        seed(db)
    print("Seed complete.")
