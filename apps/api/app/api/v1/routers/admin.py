import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.queue import (
    JOB_TYPES,
    _queue_names,
    declare_topology,
    get_connection,
    purge_dlq,
    queue_message_count,
    requeue_one_dlq_message,
)
from app.core.security import hash_password
from app.core.storage import ALL_BUCKETS, bucket_status
from app.db.session import get_db
from app.deps import require_permission
from app.models.models import AuditLog, Job, Permission, Role, ShiftDefinition, SystemConfig, User
from app.schemas.schemas import (
    AuditLogOut,
    DlqActionResult,
    DlqQueueStatus,
    JobOut,
    RoleOut,
    RoleUpdate,
    ShiftDefinitionOut,
    ShiftDefinitionUpdate,
    SkillSnapshotOut,
    StorageBucketStatusOut,
    SystemConfigOut,
    SystemConfigUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.seed import SYSTEM_CONFIG_ID
from app.skills.registry import (
    SkillVersionNotFound,
    get_or_create_snapshot,
    list_skill_names,
    list_snapshots,
    set_active_snapshot,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        enabled=user.enabled,
        last_login_at=user.last_login_at,
        roles=[r.name for r in user.roles],
        permissions=sorted(user.permission_names()),
    )


def _resolve_roles(db: Session, role_names: list[str]) -> list[Role]:
    roles = list(db.scalars(select(Role).where(Role.name.in_(role_names))))
    found = {role.name for role in roles}
    unknown = sorted(set(role_names) - found)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown roles: {', '.join(unknown)}")
    return roles


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("user.read")),
) -> list[UserOut]:
    users = list(db.scalars(select(User).order_by(User.username)))
    return [_user_out(u) for u in users]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("user.manage")),
) -> UserOut:
    if db.scalar(select(User).where(User.username == body.username)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")

    roles = _resolve_roles(db, body.role_names)
    user = User(
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        password_hash=hash_password(body.password),
        roles=roles,
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="user.create",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"username": user.username},
    )
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("user.manage")),
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    data = body.model_dump(exclude_unset=True)
    role_names = data.pop("role_names", None)
    for field, value in data.items():
        setattr(user, field, value)
    if role_names is not None:
        user.roles = _resolve_roles(db, role_names)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="user.update",
        resource_type="user",
        resource_id=str(user.id),
        metadata=body.model_dump(exclude_unset=True, mode="json"),
    )
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("role.manage")),
) -> list[RoleOut]:
    roles = list(db.scalars(select(Role).order_by(Role.name)))
    return [
        RoleOut(id=r.id, name=r.name, permissions=sorted(p.name for p in r.permissions))
        for r in roles
    ]


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: uuid.UUID,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("role.manage")),
) -> RoleOut:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    if role.name == "Admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The Administrator role cannot be edited")

    permissions = list(db.scalars(select(Permission).where(Permission.name.in_(body.permissions))))
    found = {permission.name for permission in permissions}
    unknown = sorted(set(body.permissions) - found)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown permissions: {', '.join(unknown)}")

    role.permissions = permissions
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="role.update",
        resource_type="role",
        resource_id=str(role.id),
        metadata={"name": role.name, "permissions": sorted(found)},
    )
    db.commit()
    db.refresh(role)
    return RoleOut(id=role.id, name=role.name, permissions=sorted(found))


@router.get("/shift-definitions", response_model=list[ShiftDefinitionOut])
def list_shift_definitions(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("shift.read")),
) -> list[ShiftDefinitionOut]:
    return list(db.scalars(select(ShiftDefinition).order_by(ShiftDefinition.start_time)))


@router.patch("/shift-definitions/{definition_id}", response_model=ShiftDefinitionOut)
def update_shift_definition(
    definition_id: uuid.UUID,
    body: ShiftDefinitionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("shift.manage")),
) -> ShiftDefinitionOut:
    definition = db.get(ShiftDefinition, definition_id)
    if definition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift definition not found")

    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(definition, field, value)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="shift_definition.update",
        resource_type="shift_definition",
        resource_id=str(definition.id),
        metadata=data,
    )
    db.commit()
    db.refresh(definition)
    return definition


def _get_or_create_system_config(db: Session) -> SystemConfig:
    config = db.get(SystemConfig, SYSTEM_CONFIG_ID)
    if config is None:
        # Should already exist via app/seed.py, but don't 500 a fresh DB
        # that skipped seeding — create the same default row on first read.
        config = SystemConfig(id=SYSTEM_CONFIG_ID)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.get("/system-config", response_model=SystemConfigOut)
def get_system_config(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("system.read")),
) -> SystemConfigOut:
    """Return generic worker settings for the admin UI.

    No external runtime is configured in the current phase; these settings
    remain available for a future separately deployed runtime worker.
    """
    return _get_or_create_system_config(db)


@router.patch("/system-config", response_model=SystemConfigOut)
def update_system_config(
    body: SystemConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("system.configure")),
) -> SystemConfigOut:
    config = _get_or_create_system_config(db)
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(config, field, value)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="system_config.update",
        resource_type="system_config",
        resource_id=str(config.id),
        metadata=data,
    )
    db.commit()
    db.refresh(config)
    return config


@router.get("/storage", response_model=list[StorageBucketStatusOut])
def storage_status(
    _: User = Depends(require_permission("system.read")),
) -> list[StorageBucketStatusOut]:
    """Milestone 17 gap follow-up (Storage tab, §25). Live per-bucket
    status straight from MinIO — same "system.read" gate as /admin/jobs
    and /admin/dlq, since this is likewise operational visibility, not
    configuration."""
    return [StorageBucketStatusOut(**bucket_status(bucket)) for bucket in ALL_BUCKETS]


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("system.read")),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[JobOut]:
    """§26: RabbitMQ is not the source of truth for job status — this
    reads the Job table, not the queue."""
    return list(
        db.scalars(select(Job).order_by(Job.queued_at.desc()).limit(limit).offset(offset))
    )


@router.get("/dlq", response_model=list[DlqQueueStatus])
def dlq_status(
    _: User = Depends(require_permission("system.read")),
) -> list[DlqQueueStatus]:
    """Live message counts straight from RabbitMQ's dead-letter queues
    (master plan §26 DLQs, §24 GET /admin/dlq)."""
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        return [
            DlqQueueStatus(
                queue=_queue_names(job_type)["dlq"],
                message_count=queue_message_count(channel, _queue_names(job_type)["dlq"]),
            )
            for job_type in JOB_TYPES
        ]
    finally:
        connection.close()


@router.post("/dlq/{job_type}/requeue", response_model=DlqActionResult)
def requeue_dlq(
    job_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("system.configure")),
) -> DlqActionResult:
    """Milestone 17 (DLQ controls). Pops one message off job_type's DLQ,
    republishes it onto the main queue with attempt reset to 1, and
    reconciles the Job row back to QUEUED (Postgres is the source of
    truth for job status, per §26 — RabbitMQ redelivery alone wouldn't
    update it). A no-op (0 requeued) when the DLQ is already empty."""
    if job_type not in JOB_TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown job_type")

    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        payload = requeue_one_dlq_message(channel, job_type=job_type)
    finally:
        connection.close()

    if payload is None:
        return DlqActionResult(job_type=job_type, requeued=0)

    job_id = payload.get("job_id")
    if job_id:
        # The DLQ message is already published before this database
        # reconciliation can commit. Lock the row while reading it so a
        # concurrent worker claim cannot be interleaved with the reset.
        # If the worker has already claimed/completed the job, preserve that
        # authoritative lifecycle state instead of overwriting it with
        # QUEUED.
        job = db.execute(
            select(Job).where(Job.id == uuid.UUID(job_id)).with_for_update()
        ).scalar_one_or_none()
        if job is not None:
            if job.status in {"FAILED", "RETRYING", "QUEUED"}:
                job.status = "QUEUED"
                job.attempt = 1
                job.error_code = None
                job.error_message = None
                job.started_at = None
                job.completed_at = None
                # Reliability mission Batch A: clear any stale claim/lease so
                # the idempotent job lifecycle module's claim_job is willing
                # to claim this job again rather than treating it as still
                # leased by whatever worker had it before.
                job.claimed_at = None
                job.claim_token = None
                job.lease_expires_at = None
                job.worker_id = None

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="dlq.requeue",
        resource_type="job",
        resource_id=job_id or "",
        metadata={"job_type": job_type},
    )
    db.commit()
    return DlqActionResult(job_type=job_type, requeued=1)


@router.post("/dlq/{job_type}/purge", response_model=DlqActionResult)
def purge_dlq_endpoint(
    job_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("system.configure")),
) -> DlqActionResult:
    """Milestone 17 (DLQ controls). Permanently discards every message on
    job_type's DLQ. The corresponding Job rows stay FAILED — this is a
    deliberate "give up on these" action, not a requeue, so it doesn't
    touch Postgres beyond the audit trail."""
    if job_type not in JOB_TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown job_type")

    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        purged = purge_dlq(channel, job_type=job_type)
    finally:
        connection.close()

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="dlq.purge",
        resource_type="job_type",
        resource_id=job_type,
        metadata={"purged": purged},
    )
    db.commit()
    return DlqActionResult(job_type=job_type, purged=purged)


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("audit.read")),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogOut]:
    return list(
        db.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        )
    )


@router.get("/skills", response_model=list[str])
def list_skills(
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("skill.manage")),
) -> list[str]:
    """Every skill_name with at least one recorded snapshot. Ensures each
    known skill (log-triage-summary, daily-alert-report) has a snapshot for
    its current on-disk content before listing, so a skill that has never
    been dispatched yet still shows up here."""
    # Local imports: avoids importing every other router at module load
    # time just for these two skill-name constants.
    from app.api.v1.routers.analysis import SKILL_NAME as _LOG_TRIAGE_SKILL_NAME
    from app.api.v1.routers.reports import SKILL_NAME as _DAILY_REPORT_SKILL_NAME

    for skill_name in (_LOG_TRIAGE_SKILL_NAME, _DAILY_REPORT_SKILL_NAME):
        get_or_create_snapshot(db, skill_name)
    db.commit()
    return list_skill_names(db)


@router.get("/skills/{skill_name}/versions", response_model=list[SkillSnapshotOut])
def list_skill_versions(
    skill_name: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("skill.manage")),
) -> list[SkillSnapshotOut]:
    snapshots = list_snapshots(db, skill_name)
    if not snapshots:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No snapshots recorded for skill {skill_name!r}")
    return snapshots


@router.post("/skills/{skill_name}/versions/{version_label}/activate", response_model=SkillSnapshotOut)
def activate_skill_version(
    skill_name: str,
    version_label: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("skill.manage")),
) -> SkillSnapshotOut:
    """Atomically publish one validated immutable snapshot for new jobs.
    Existing jobs retain their previously selected snapshot."""
    try:
        snapshot = set_active_snapshot(db, skill_name, version_label)
    except (SkillVersionNotFound, ValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="skill.activate",
        resource_type="skill_snapshot",
        resource_id=str(snapshot.id),
        metadata={
            "skill_name": skill_name,
            "version_label": version_label,
            "content_hash": snapshot.content_hash,
        },
    )
    db.commit()
    db.refresh(snapshot)
    return snapshot
