import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Column,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

"""
SQLAlchemy models. Milestone 8 (Backend Core) added: users, roles/
permissions, shifts, incidents, audit. Milestone 9 (MinIO) adds:
evidence (§22.6). Milestone 10 (OCR) adds: ocr_runs (§22.7). Milestone 11
(RabbitMQ) adds: jobs (§22.8). Milestone 13 (Real Log Triage) adds:
analysis_runs (§22.9). Milestone 14 (Real Daily Report) adds:
report_snapshots, reports (§22.10, §22.11). Master plan §22 also defines
incident_relationships — that belongs to a later milestone and is
deliberately not modeled here yet (coding-agent rule 20: build only what
the current milestone needs).
"""


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --- RBAC (master plan §23) -------------------------------------------------

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", UUID(as_uuid=True), ForeignKey("permissions.id"), primary_key=True),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    permissions: Mapped[list["Permission"]] = relationship(
        secondary=role_permissions, back_populates="roles"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    roles: Mapped[list["Role"]] = relationship(
        secondary=role_permissions, back_populates="permissions"
    )


# --- Users (master plan §22.1) ----------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    roles: Mapped[list["Role"]] = relationship(secondary=user_roles)

    def permission_names(self) -> set[str]:
        return {perm.name for role in self.roles for perm in role.permissions}


# --- Shifts (master plan §22.3, §22.4) --------------------------------------


class ShiftDefinition(Base):
    __tablename__ = "shift_definitions"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_time: Mapped[str] = mapped_column(String(8), nullable=False)  # "HH:MM:SS"
    end_time: Mapped[str] = mapped_column(String(8), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Manila")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shift_definitions.id"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    opened_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    definition: Mapped["ShiftDefinition"] = relationship()


# --- AI / Claude configuration (Milestone 17 gap follow-up) -----------------
#
# Admin's "AI Configuration" tab, per the operator's explicit scope choice:
# "real config, live-wired to the bridge" — not just admin-page display, the
# bridge (bridge/noc_bridge/service.py) actually reads this table and applies
# it when dispatching sandbox jobs. Single-row table (id is always the same
# fixed UUID, see app/seed.py) rather than a key/value table — there are
# exactly four known settings and no near-term need for arbitrary new ones.


class SystemConfig(Base):
    __tablename__ = "system_config"

    id: Mapped[uuid.UUID] = uuid_pk()
    default_model: Mapped[str] = mapped_column(String(100), nullable=False, default="claude-sonnet-5")
    # Cost-optimization mission Phase 4: low effort is the default for every
    # job; the bridge escalates to a higher tier per-job only when a low-
    # effort result signals it isn't sufficient (sandbox/entrypoint.py's
    # _escalation_reason). Existing rows keep whatever value they already
    # have — this only changes the default for a newly-seeded row.
    default_effort: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    job_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Incidents (master plan §22.5, trimmed to Milestone 8 fields) ----------


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = uuid_pk()
    display_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    service: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    alert_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    teams_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    grafana_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Evidence (master plan §22.6, §25) --------------------------------------


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=True
    )


# --- OCR Runs (master plan §22.7, §12) --------------------------------------


class OcrRun(Base):
    __tablename__ = "ocr_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(50), nullable=False, default="paddleocr")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    extracted_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_text: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Jobs (master plan §22.8, §26) -------------------------------------------


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    # Milestone 13 addition: §22.8's Jobs schema doesn't list incident_id
    # (RabbitMQ messages carry it per ADR-004, but nothing before this
    # milestone needed to query "jobs for this incident" from Postgres).
    # Nullable because daily_report jobs (Milestone 14) aren't
    # incident-scoped.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Analysis Runs (master plan §22.9, Milestone 13) ------------------------


class AnalysisRun(Base):
    """"Never overwrite old runs" (§22.9): one row per triage attempt,
    created at job-enqueue time (so `input_manifest_sha256`/
    `log_evidence_id` are captured against what was actually submitted),
    with `result_json`/`result_text`/`output_sha256` filled in exactly
    once when the job completes — never overwritten after that. `current`
    marks the latest run per incident so the UI can find it without
    scanning history."""

    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, unique=True
    )
    log_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id"), nullable=True
    )
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_text: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    input_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Report Snapshots / Reports (master plan §22.10, §22.11, Milestone 14) -


class ReportSnapshot(Base):
    """Freezes report inputs at generation time (§22.10) — the snapshot a
    given Report was generated from never changes after creation, even if
    the underlying incidents/analysis runs are edited later."""

    __tablename__ = "report_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_snapshots.id"), nullable=False
    )
    # Milestone 14 addition: §22.11's Reports schema doesn't list job_id,
    # but polling needs to correlate a Report row back to the Job the
    # bridge is (or was) working on — same precedent as Job.incident_id
    # in Milestone 13.
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    report_bucket: Mapped[str | None] = mapped_column(String(100), nullable=True)
    report_object_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Audit (master plan §22.13) ---------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
