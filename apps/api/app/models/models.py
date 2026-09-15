import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Column,
    Index,
    UniqueConstraint,
    text,
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


class RefreshSession(Base):
    """Server-owned, rotatable refresh credential session.

    Only a SHA-256 digest is stored. The raw credential exists solely in the
    HttpOnly browser cookie and a refresh rotates the row before issuing its
    replacement.
    """

    __tablename__ = "refresh_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    __table_args__ = (
        Index(
            "uq_shifts_one_active",
            "state",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
    )

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
    normalized_text: Mapped[str | None] = mapped_column(String, nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ocr_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefill_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prefill_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    prefill_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prefill_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefill_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IncidentPrefillRun(Base):
    """Pre-incident screenshot OCR and evidence-backed field suggestions.

    A prefill exists before an Incident exists, so it cannot reuse the
    incident-owned Evidence/OcrRun pair. The source screenshot remains in
    object storage and is attached to the created Incident only after the
    operator confirms the form.
    """

    __tablename__ = "incident_prefill_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )
    source_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    source_object_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_ocr_text: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_ocr_text: Mapped[str | None] = mapped_column(String, nullable=True)
    ocr_engine: Mapped[str] = mapped_column(String(50), nullable=False, default="paddleocr")
    ocr_engine_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ocr_extraction_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prefill_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PROCESSING")
    prefill_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ocr_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefill_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
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
    # Immutable execution contract selected when the job was created.  The
    # display fields above remain for backwards-compatible reporting only.
    skill_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_snapshots.id"), nullable=False
    )
    # Skill Registry (Reliability mission Batch B): the content hash of
    # the SkillSnapshot actually used for this row, computed by
    # app/skills/registry.py at enqueue time. skill_name/skill_version
    # remain a human-readable label; skill_hash is the tamper-evident
    # identity a cache lookup and the bridge's pre-execution check both
    # key off of.
    skill_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Full immutable execution identity: this snapshot's content plus its
    # pinned transitive dependency graph.  skill_hash remains the exact
    # row-content hash used to verify materialized bytes.
    skill_execution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AI cost-optimization mission Phase 6: set when this job was satisfied
    # by reusing a prior identical-input AnalysisRun instead of invoking
    # Claude at all (see app/api/v1/routers/analysis.py's exact-cache
    # lookup). cache_type is "exact" for now; a later pattern-cache phase
    # may add other values.
    used_cache: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Paid Claude calls are a separate resource from RabbitMQ/MinIO retry
    # attempts. The bridge reserves this budget atomically before launching
    # the sandbox; a redelivery cannot reset it or purchase another set of
    # model calls. Four permits the current two structured-output tries plus
    # one bounded LOW->MEDIUM escalation (which can itself use two tries).
    paid_ai_call_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    paid_ai_calls_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paid_ai_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Reliability mission Batch A (idempotent job lifecycle): a claim/lease
    # so the bridge can tell "am I the one allowed to execute this job right
    # now" apart from RabbitMQ's own at-least-once redelivery. `claim_token`
    # is a fresh uuid4 minted by whichever worker successfully claims the
    # job (see bridge/noc_bridge/db.claim_job's conditional UPDATE); a
    # redelivery that arrives while the lease is still live is not
    # reclaimable and the bridge skips re-executing it. `lease_expires_at`
    # lets a crashed worker's claim eventually become reclaimable again
    # instead of wedging the job forever.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


# --- Outbox (Reliability mission Batch A: transactional outbox) ------------


class OutboxEvent(Base):
    """A row here is created in the *same* Postgres transaction as the
    Job (and AnalysisRun/Report/ReportSnapshot) it announces, so a
    RabbitMQ publish can never observably happen before the domain state
    it describes is durable — the dispatcher (app/outbox.py) only
    publishes rows that already committed. `published_at` is set once the
    broker has confirmed the publish; a row with `published_at is None` is
    still due and safe to retry (publishing is at-least-once, matching
    RabbitMQ's own delivery guarantee — the job lifecycle module handles
    the resulting idempotency on the consumer side)."""

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    routing_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)


class SkillSnapshot(Base):
    """Skill Registry (Reliability mission Batch B): an immutable record
    of one exact version of a skill's content (its SKILL.md prompt,
    output.schema.json contract, and skill.yaml manifest, all captured
    verbatim here). Rows are never updated — a content change produces a
    *new* row with a new `content_hash` and the next `version_label`,
    computed by `app/skills/registry.py::get_or_create_snapshot`. This is
    what makes `skill_hash` (stored on Job/AnalysisRun/Report) a real,
    tamper-evident identity instead of a free-text label someone has to
    remember to bump."""

    __tablename__ = "skill_snapshots"
    __table_args__ = (
        Index(
            "uq_skill_snapshots_one_active_per_name",
            "skill_name",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version_label: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # A dependency-aware identity; unlike content_hash it changes when a
    # pinned dependency changes even if this snapshot's own files do not.
    execution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_md: Mapped[str] = mapped_column(String, nullable=False)
    output_schema_json: Mapped[str] = mapped_column(String, nullable=False)
    manifest_yaml: Mapped[str] = mapped_column(String, nullable=False)
    dependency_snapshot_ids: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Skill Admin activation workflow (Phase 12, Batch D): the snapshot a
    # skill_name currently resolves to for *new* jobs. Exactly one active
    # snapshot per skill_name at a time; older/newer inactive snapshots
    # stay in the table as an immutable history/rollback target.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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
    __table_args__ = (
        Index(
            "uq_analysis_runs_current_incident",
            "incident_id",
            unique=True,
            postgresql_where=text("current IS TRUE"),
        ),
    )

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
    # Skill Registry (Reliability mission Batch B): the content hash of
    # the SkillSnapshot actually used for this row, computed by
    # app/skills/registry.py at enqueue time. skill_name/skill_version
    # remain a human-readable label; skill_hash is the tamper-evident
    # identity a cache lookup and the bridge's pre-execution check both
    # key off of.
    skill_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Skill Runtime mission Phase 6: a direct FK to the exact
    # skill_snapshots row used, not just its hash — one join away from
    # the run's full provenance (SKILL.md prose, output schema, manifest,
    # activation history) instead of a second hash-keyed lookup.
    skill_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_snapshots.id"), nullable=True
    )
    skill_execution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_contract_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    preprocessor_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_policy_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_policy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # AI cost-optimization mission Phase 6 — mirrors Job.used_cache/
    # cache_type (see there) so a cache-hit run is visible from either row.
    used_cache: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # AI cost-optimization mission Phase 1 (usage telemetry) — populated
    # from the sandbox's telemetry.json (see sandbox/entrypoint.py's
    # run_skill / _envelope_telemetry) when `_sync_completed_job` syncs a
    # completed job's result. All nullable: telemetry is best-effort and
    # must never block a real analysis result from being usable. Left
    # unset (None/False) on a cache hit, since no Claude call happened.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_model_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_input_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preprocessing_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cache compatibility axes for runtime code outside the immutable skill
    # contract (currently the deterministic preprocessor and AI policy).
    # The output schema and skill instructions are already covered by
    # skill_snapshot_id/content_hash, so ordinary skill changes do not need
    # an unrelated cache-version bump.
    cache_contract_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # AI cost-optimization mission Phase 2, Issue 4 (cumulative escalation
    # telemetry): a LOW->MEDIUM escalated job makes two real Claude calls,
    # and total usage must be the sum of both, not just the escalated
    # call's. `estimated_cost_usd`/`input_tokens`/etc above already hold
    # the totals (see sandbox/entrypoint.py's run_skill); these hold the
    # initial (always-made) attempt's own numbers separately, so a
    # dashboard can show "how much did the low-effort attempt alone cost"
    # vs. "how much did escalation add" — both nullable since a non-
    # escalated run only ever made the one (initial) call, which is
    # already fully represented by the totals above.
    attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    initial_effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    initial_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The escalation (second) call's own numbers, populated only when
    # `escalated` is true. The `total_*` concept the mission asks for is
    # represented by the existing top-level fields above (input_tokens,
    # estimated_cost_usd, etc.) — those are now computed as
    # initial + escalation (see sandbox/entrypoint.py's run_skill), so
    # there is no separate total_* column set duplicating them.
    escalation_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    escalation_effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    escalation_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


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
    skill_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_snapshots.id"), nullable=True
    )
    skill_execution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("shift_id", "version", name="uq_reports_shift_version"),
    )

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
    skill_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_snapshots.id"), nullable=True
    )
    # Skill Registry (Reliability mission Batch B): the content hash of
    # the SkillSnapshot actually used for this row, computed by
    # app/skills/registry.py at enqueue time. skill_name/skill_version
    # remain a human-readable label; skill_hash is the tamper-evident
    # identity a cache lookup and the bridge's pre-execution check both
    # key off of.
    skill_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_execution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
