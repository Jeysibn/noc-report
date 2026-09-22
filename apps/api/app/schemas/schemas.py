import uuid
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai_policy import CONCURRENCY_MAX, CONCURRENCY_MIN, TIMEOUT_MAX, TIMEOUT_MIN

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    """Deprecated compatibility body; refresh credentials now use a cookie."""
    pass


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    enabled: bool
    last_login_at: datetime | None
    roles: list[str] = []
    permissions: list[str] = []


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    password: str = Field(min_length=8, max_length=200)
    role_names: list[str] = []


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = None
    enabled: bool | None = None
    role_names: list[str] | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    permissions: list[str]


class ShiftDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    start_time: str
    end_time: str
    timezone: str
    enabled: bool


class ShiftDefinitionUpdate(BaseModel):
    """Milestone 17 gap follow-up (Shift Configuration). Times are
    "HH:MM:SS" strings, matching the model's storage format — not parsed
    further server-side (validated the same loosely-typed way the model
    column itself is)."""

    start_time: str | None = None
    end_time: str | None = None
    timezone: str | None = None
    enabled: bool | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_clock_time(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                time.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("time must use HH:MM[:SS] format") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shift_definition_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime | None
    state: str
    opened_by: uuid.UUID | None
    closed_by: uuid.UUID | None


class ShiftOpen(BaseModel):
    """Body for POST /shifts/open. shift_definition_id is optional — most
    of the time the caller just wants "start a shift right now" and
    doesn't care which named definition (Day/Swing/Night) it's filed
    under, so it defaults to whichever enabled definition matches the
    current wall-clock time, falling back to the first enabled one."""

    shift_definition_id: uuid.UUID | None = None


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    service: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=100)
    status: Literal["open", "investigating", "recovered"] = "open"
    alert_source: str | None = None
    triggered_at: datetime
    recovered_at: datetime | None = None
    trigger_value: str | None = None
    teams_url: str | None = None
    grafana_url: str | None = None
    notes: str | None = None


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["open", "investigating", "recovered"] | None = None
    recovered_at: datetime | None = None
    notes: str | None = None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_id: str
    shift_id: uuid.UUID | None
    title: str
    service: str
    environment: str
    status: str
    alert_source: str | None
    triggered_at: datetime
    recovered_at: datetime | None
    trigger_value: str | None
    teams_url: str | None
    grafana_url: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    # Derived, not stored columns — computed in the router (see
    # app/api/v1/routers/incidents.py's `_attach_derived`) from a batched
    # Evidence/AnalysisRun+Job query so list() stays O(1) queries instead
    # of N+1, while still reporting the real state (previously list()
    # hardcoded has_log=False/analysis_status="not_analyzed" for every
    # row, which was wrong whenever a log was actually attached).
    has_log: bool = False
    analysis_status: str = "not_analyzed"


class IncidentPage(BaseModel):
    items: list[IncidentOut]
    total: int


class DashboardSummaryOut(BaseModel):
    open_incidents: int
    active_alerts: int
    recovered_alerts: int
    logs_awaiting_analysis: int
    analyses_running: int
    reports_generated_this_shift: int


class EvidenceUploadUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: Literal["ALERT_SCREENSHOT", "LOG", "GRAFANA_SCREENSHOT", "SUPPORTING_DOCUMENT", "OTHER"]
    filename: str = Field(min_length=1, max_length=500)
    content_type: str | None = None
    expected_byte_size: int | None = Field(default=None, ge=0)


class EvidenceUploadUrlResponse(BaseModel):
    upload_id: uuid.UUID
    upload_url: str
    expires_at: datetime


class EvidenceCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: uuid.UUID


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID | None
    evidence_type: str
    original_filename: str
    mime_type: str | None
    byte_size: int | None
    created_at: datetime
    lifecycle_state: str
    deleted_at: datetime | None


class EvidenceDownloadUrlResponse(BaseModel):
    download_url: str


class OcrRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    evidence_id: uuid.UUID
    engine: str
    status: str
    extracted_json: dict
    raw_text: str | None
    normalized_text: str | None = None
    engine_version: str | None = None
    ocr_duration_ms: int | None = None
    prefill_json: dict | None = None
    prefill_status: str | None = None
    prefill_model: str | None = None
    prefill_duration_ms: int | None = None
    prefill_error: str | None = None
    created_at: datetime
    completed_at: datetime | None


class IncidentPrefillOut(BaseModel):
    id: uuid.UUID
    status: str
    source_filename: str
    source_sha256: str
    raw_ocr_text: str | None
    normalized_ocr_text: str | None
    ocr_engine: str
    ocr_engine_version: str | None
    ocr_duration_ms: int | None
    prefill_json: dict | None
    prefill_model: str | None
    prefill_duration_ms: int | None
    error_message: str | None
    incident_id: uuid.UUID | None
    created_at: datetime
    completed_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: str
    requested_by: uuid.UUID | None
    model: str | None
    effort: str | None
    skill_name: str | None
    skill_version: str | None
    skill_snapshot_id: uuid.UUID
    skill_hash: str | None = None
    skill_execution_hash: str | None = None
    attempt: int
    correlation_id: str
    error_code: str | None
    error_message: str | None
    paid_ai_call_budget: int
    paid_ai_calls_reserved: int
    paid_ai_calls_used: int
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DlqQueueStatus(BaseModel):
    queue: str
    message_count: int


class DlqActionResult(BaseModel):
    """Milestone 17 (DLQ controls). `requeued`/`purged` are message
    counts, not booleans, so an empty DLQ (nothing to act on) is
    distinguishable from an actual action having happened."""

    job_type: str
    requeued: int = 0
    purged: int = 0


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisRunOut(BaseModel):
    """Merges the Job row (in-flight status) with the AnalysisRun row
    (result, once synced from MinIO on job completion — see
    app/api/v1/routers/analysis.py's `_sync_completed_job`). Built by
    hand in the router rather than `from_attributes`, since it spans two
    tables."""

    job_id: uuid.UUID
    incident_id: uuid.UUID | None
    status: str
    model: str | None
    effort: str | None
    runtime_name: str | None = None
    runtime_version: str | None = None
    runtime_profile: str | None = None
    provider: str | None = None
    runtime_model: str | None = None
    skill_name: str | None
    skill_version: str | None
    # Skill Runtime mission Phase 6: the exact SkillSnapshot row's id, not
    # just the human-readable name/version label.
    skill_snapshot_id: uuid.UUID | None = None
    skill_hash: str | None = None
    skill_execution_hash: str | None = None
    schema_hash: str | None = None
    input_contract_version: str | None = None
    preprocessor_version: str | None = None
    ai_policy_version: str | None = None
    ai_policy_json: dict | None = None
    attempt: int
    error_code: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    run_id: uuid.UUID | None = None
    result: dict | None = None
    current: bool = False
    # AI cost-optimization mission Phase 6 (exact-match result cache).
    used_cache: bool = False
    cache_type: str | None = None
    # AI cost-optimization mission Phase 1 (usage telemetry) — null when
    # unavailable (older run, cache hit, telemetry upload failure).
    input_tokens: int | None = None
    total_model_input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    estimated_cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    confidence: float | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    raw_input_bytes: int | None = None
    evidence_bytes: int | None = None
    preprocessing_ratio: float | None = None
    # AI cost-optimization mission Phase 2, Issue 4 (cumulative escalation
    # telemetry): the fields above (input_tokens/estimated_cost_usd/etc)
    # are now totals (initial + escalation, when escalated); these expose
    # the initial and escalation attempts individually.
    attempt_count: int | None = None
    initial_model: str | None = None
    initial_effort: str | None = None
    initial_input_tokens: int | None = None
    initial_output_tokens: int | None = None
    initial_cache_read_tokens: int | None = None
    initial_cache_creation_tokens: int | None = None
    initial_duration_ms: int | None = None
    initial_estimated_cost_usd: float | None = None
    escalation_model: str | None = None
    escalation_effort: str | None = None
    escalation_input_tokens: int | None = None
    escalation_output_tokens: int | None = None
    escalation_cache_read_tokens: int | None = None
    escalation_cache_creation_tokens: int | None = None
    escalation_duration_ms: int | None = None
    escalation_estimated_cost_usd: float | None = None


class ReportGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportOut(BaseModel):
    """Merges the immutable Report row with the authoritative Job status.

    Report lifecycle state belongs to ``Job.status``; the Report row stores
    identity and pinned artifact metadata rather than a second mutable status.
    """

    id: uuid.UUID
    shift_id: uuid.UUID
    snapshot_id: uuid.UUID
    job_id: uuid.UUID
    version: int
    status: str
    model: str | None
    effort: str | None
    skill_name: str | None
    skill_version: str | None
    skill_snapshot_id: uuid.UUID | None = None
    skill_hash: str | None = None
    skill_execution_hash: str | None = None
    generated_by: uuid.UUID | None
    generated_at: datetime | None
    error_message: str | None
    created_at: datetime
    downloadable: bool = False
    previewable: bool = False
    report_version_id: str | None = None


class ReportDownloadUrlResponse(BaseModel):
    download_url: str


class SearchResultOut(BaseModel):
    id: uuid.UUID
    display_id: str
    title: str
    service: str
    environment: str
    status: str
    triggered_at: datetime
    has_log: bool
    analysis_summary: str | None
    recurrence_count: int


class SearchResponse(BaseModel):
    items: list[SearchResultOut]
    total: int


class BarDatumOut(BaseModel):
    label: str
    value: int


class DonutSliceOut(BaseModel):
    label: str
    value: int


class TopAlertTitleOut(BaseModel):
    title: str
    count: int


class ReportGenerationCountsOut(BaseModel):
    this_shift: int
    today: int
    this_week: int


class AnalyticsSummaryOut(BaseModel):
    """Master plan §17 V1 analytics. `top_exception_signatures` is omitted:
    exception/error signature isn't a modeled field anywhere in the schema
    yet (same rule-20 call as Milestone 15's search index), so it isn't
    fabricated here."""

    incidents_by_day: list[BarDatumOut]
    incidents_by_shift: list[BarDatumOut]
    alerts_by_service: list[BarDatumOut]
    recovered_vs_unresolved: list[DonutSliceOut]
    analysis_job_outcomes: list[DonutSliceOut]
    top_recurring_alert_titles: list[TopAlertTitleOut]
    report_generation_counts: ReportGenerationCountsOut


class SystemConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_timeout_seconds: int
    max_concurrent_jobs: int
    updated_at: datetime


class SystemConfigUpdate(BaseModel):
    """Milestone 17 gap follow-up (AI Configuration). All fields optional —
    partial-update semantics via `exclude_unset`, same as ShiftDefinitionUpdate."""

    job_timeout_seconds: int | None = Field(default=None, ge=TIMEOUT_MIN, le=TIMEOUT_MAX)
    max_concurrent_jobs: int | None = Field(
        default=None, ge=CONCURRENCY_MIN, le=CONCURRENCY_MAX
    )


class StorageBucketStatusOut(BaseModel):
    """Milestone 17 gap follow-up (Storage tab). Object count/size are a
    live listing sum, not a stored counter — see app/core/storage.py's
    `bucket_status`."""

    bucket: str
    versioning_enabled: bool
    object_count: int
    total_bytes: int


class SkillSnapshotOut(BaseModel):
    """Skill Registry admin activation workflow (Phase 12)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_name: str
    version_label: int
    content_hash: str
    is_active: bool
    created_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str
    metadata_json: dict
    created_at: datetime


class IncidentTimelineEventOut(BaseModel):
    """One row in an incident's real timeline. Derived on read from
    existing timestamped columns (Incident/Evidence/Job) — not a separate
    event log, so it can never drift out of sync with the rows it
    describes (see app/api/v1/routers/incidents.py's get_timeline)."""

    event_type: str
    label: str
    occurred_at: datetime
    detail: dict | None = None
