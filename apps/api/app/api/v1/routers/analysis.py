"""Milestone 13 — Real Log Triage (master plan pipeline):

    Analyze Log -> API -> PostgreSQL Job -> RabbitMQ -> Bridge -> Docker
    -> Claude Code -> log-triage-summary -> MinIO/PostgreSQL -> UI

No fallback analyzer in the API — if the bridge/sandbox pipeline isn't
running, a request just stays QUEUED (visible truthfully as such), it
never fabricates a result.
"""
import json
import hashlib
import pathlib
import uuid
import yaml
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.core.config import settings
from app.core.storage import get_object_bytes, sha256_of_bytes
from app.db.session import get_db
from app.deps import require_permission
from app.jobs import enqueue_job
from app.models.models import AnalysisRun, Evidence, Incident, Job, User
from app.skills.registry import compute_execution_hash, resolve_active_snapshot
from app.skills.runtime import declared_skill_version, execution_policy, input_contract_version
from app.schemas.schemas import AnalysisRequest, AnalysisRunOut

router = APIRouter(tags=["analysis"])

SKILL_NAME = "log-triage-summary"

# Compatibility label for older callers/tests that construct a Job directly.
# Runtime job creation never uses this value to select a skill; it resolves a
# SkillSnapshot and derives the label from that immutable manifest. Reading it
# from the source manifest avoids retaining a second hard-coded version.
_SOURCE_MANIFEST = pathlib.Path(__file__).resolve().parents[6] / "skills" / SKILL_NAME / "skill.yaml"
SKILL_VERSION = str((yaml.safe_load(_SOURCE_MANIFEST.read_text()) or {}).get("version", "1"))

# AI cost-optimization mission Phase 2, Issue 5 (cache versioning):
# The manifest-declared version is descriptive; immutable snapshot identity
# is the execution/cache identity. This avoids an unrelated hard-coded
# version string diverging from the active skill.
# The old SKILL_VERSION label conflated independently-changing axes into one
# number. Snapshot content now covers the skill-owned prompt/schema/manifest;
# these constants cover only application-owned preprocessing and AI policy.
# They are stored separately on AnalysisRun and combined for the cache query.
# Bump the specific constant that actually changed:
#   PREPROCESSOR_VERSION     — sandbox/entrypoint.py's log-compaction/
#                              structured-evidence-extraction behavior
#                              changes what evidence Claude actually sees
#                              for the same raw input.
#   AI_POLICY_VERSION        — the model/effort/escalation policy changes
#                              (e.g. a new escalation trigger, a changed
#                              confidence threshold, a different default
#                              model) in a way that could change what a
#                              given input produces even with an unchanged
#                              schema and preprocessor.
# SKILL_VERSION remains a compatibility display alias for older API clients;
# the immutable snapshot hash, not this label, selects execution or cache data.
PREPROCESSOR_VERSION = "1"
AI_POLICY_VERSION = "1"
CACHE_CONTRACT_VERSION = f"{PREPROCESSOR_VERSION}.{AI_POLICY_VERSION}"


def _find_cached_analysis_run(
    db: Session,
    *,
    log_evidence: Evidence,
    requested_model: str | None,
    requested_effort: str | None,
    skill_hash: str,
    skill_execution_hash: str | None = None,
    skill_snapshot_id: uuid.UUID | None = None,
) -> AnalysisRun | None:
    """Exact-match cache lookup: a prior *completed* log-triage-summary run
    against the same evidence checksum, produced under the same skill
    version AND the same cache contract version (schema/preprocessor/AI
    policy all unchanged since), is reusable verbatim — this is the same
    log, analyzed the same way, so re-running Claude on it would just
    reproduce the same result at full cost. Never reuses a run that hasn't
    completed yet (result_json is only set once a job actually finishes —
    see `_sync_completed_job`).

    Also respects an explicit operator override: if the caller asked for a
    specific model or effort, a cached run produced under a *different*
    model/effort is not an equivalent result and must not be silently
    served in its place — that would defeat the whole point of asking for
    a higher effort tier. A request that leaves model/effort unset (the
    common case) matches any cached run's model/effort.

    Skill Registry (Reliability mission Batch B) / Skill Runtime mission
    Phase 16: `skill_hash` — not `skill_version` — is the cache key's
    content-identity check. `skill_version` is a human-chosen display
    label (see app/skills/registry.py's own docstring: "no manual version
    bump required to get that safety") that can drift from the skill's
    actual on-disk content independently of `skill_hash`, which is
    mechanically derived from SKILL.md + output.schema.json + skill.yaml's
    literal bytes. Keying the cache on the label instead of (or as well
    as) the hash would reintroduce exactly the "forgot to bump the
    version" drift the Skill Registry exists to close — a content change
    without a version bump must still miss the cache (via skill_hash), and
    a version-label-only change with no content change should still hit
    it (harmless, but the point is skill_hash alone is both necessary and
    sufficient for content-identity correctness here)."""
    if not log_evidence.sha256:
        return None
    filters = [
        AnalysisRun.skill_name == SKILL_NAME,
        AnalysisRun.skill_hash == skill_hash,
        AnalysisRun.cache_contract_version == CACHE_CONTRACT_VERSION,
        AnalysisRun.result_json.is_not(None),
    ]
    if skill_execution_hash is not None:
        filters.append(AnalysisRun.skill_execution_hash == skill_execution_hash)
    if skill_snapshot_id is not None:
        filters.append(AnalysisRun.skill_snapshot_id == skill_snapshot_id)
    if requested_model is not None:
        filters.append(AnalysisRun.model == requested_model)
    if requested_effort is not None:
        filters.append(AnalysisRun.effort == requested_effort)
    return db.scalar(
        select(AnalysisRun).where(*filters).order_by(AnalysisRun.created_at.desc())
    )


def _snapshot_schema_hash(snapshot) -> str:
    return hashlib.sha256(snapshot.output_schema_json.encode("utf-8")).hexdigest()


def _snapshot_runtime_provenance(snapshot) -> dict:
    return {
        "input_contract_version": input_contract_version(snapshot),
        "preprocessor_version": PREPROCESSOR_VERSION,
        "ai_policy_version": AI_POLICY_VERSION,
        "ai_policy_json": execution_policy(snapshot),
    }


def _get_incident_or_404(db: Session, incident_id: uuid.UUID) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


def _to_out(job: Job, run: AnalysisRun | None) -> AnalysisRunOut:
    return AnalysisRunOut(
        job_id=job.id,
        incident_id=job.incident_id,
        status=job.status,
        model=job.model,
        effort=job.effort,
        skill_name=job.skill_name,
        skill_version=job.skill_version,
        skill_snapshot_id=run.skill_snapshot_id if run else None,
        skill_hash=run.skill_hash if run else None,
        skill_execution_hash=run.skill_execution_hash if run else None,
        schema_hash=run.schema_hash if run else None,
        input_contract_version=run.input_contract_version if run else None,
        preprocessor_version=run.preprocessor_version if run else None,
        ai_policy_version=run.ai_policy_version if run else None,
        ai_policy_json=run.ai_policy_json if run else None,
        attempt=job.attempt,
        error_code=job.error_code,
        error_message=job.error_message,
        queued_at=job.queued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        run_id=run.id if run else None,
        result=run.result_json if run else None,
        current=run.current if run else False,
        used_cache=job.used_cache,
        cache_type=job.cache_type,
        input_tokens=run.input_tokens if run else None,
        output_tokens=run.output_tokens if run else None,
        cache_creation_tokens=run.cache_creation_tokens if run else None,
        cache_read_tokens=run.cache_read_tokens if run else None,
        estimated_cost_usd=run.estimated_cost_usd if run else None,
        duration_ms=run.duration_ms if run else None,
        num_turns=run.num_turns if run else None,
        confidence=run.confidence if run else None,
        escalated=run.escalated if run else False,
        escalation_reason=run.escalation_reason if run else None,
        raw_input_bytes=run.raw_input_bytes if run else None,
        evidence_bytes=run.evidence_bytes if run else None,
        preprocessing_ratio=run.preprocessing_ratio if run else None,
        attempt_count=run.attempt_count if run else None,
        initial_model=run.initial_model if run else None,
        initial_effort=run.initial_effort if run else None,
        initial_input_tokens=run.initial_input_tokens if run else None,
        initial_output_tokens=run.initial_output_tokens if run else None,
        initial_cache_read_tokens=run.initial_cache_read_tokens if run else None,
        initial_cache_creation_tokens=run.initial_cache_creation_tokens if run else None,
        initial_duration_ms=run.initial_duration_ms if run else None,
        initial_estimated_cost_usd=run.initial_estimated_cost_usd if run else None,
        escalation_model=run.escalation_model if run else None,
        escalation_effort=run.escalation_effort if run else None,
        escalation_input_tokens=run.escalation_input_tokens if run else None,
        escalation_output_tokens=run.escalation_output_tokens if run else None,
        escalation_cache_read_tokens=run.escalation_cache_read_tokens if run else None,
        escalation_cache_creation_tokens=run.escalation_cache_creation_tokens if run else None,
        escalation_duration_ms=run.escalation_duration_ms if run else None,
        escalation_estimated_cost_usd=run.escalation_estimated_cost_usd if run else None,
    )


def _sync_completed_job(db: Session, job: Job) -> AnalysisRun | None:
    """A completed `log_triage` Job has its result sitting in
    `noc-job-artifacts` (the bridge never writes into Postgres directly —
    see bridge/noc_bridge/service.py). The first poll to observe
    status=="COMPLETED" pulls that result down and fills in the
    AnalysisRun row created at enqueue time; every later poll is a no-op
    since `result_json` is only ever set once (§22.9: "never overwrite
    old runs")."""
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.job_id == job.id))
    if run is None or run.result_json is not None:
        return run
    if job.status != "COMPLETED":
        return run

    key = f"jobs/{job.id}/result.json"
    try:
        body = get_object_bytes(settings.minio_bucket_job_artifacts, key)
    except ClientError:
        # Bridge marked the Job COMPLETED but the artifact isn't visible
        # yet (or was never written) — leave the run unfilled rather than
        # fabricate a result; the next poll tries again.
        return run

    run.result_json = json.loads(body)
    run.result_text = body.decode("utf-8", errors="replace")
    run.output_sha256 = sha256_of_bytes(body)

    # Phase 1 (AI usage telemetry): best-effort — an older run or a run
    # where the bridge couldn't upload telemetry.json simply leaves these
    # fields null, same as any other missing telemetry field.
    try:
        telemetry_body = get_object_bytes(settings.minio_bucket_job_artifacts, f"jobs/{job.id}/telemetry.json")
        telemetry = json.loads(telemetry_body)
    except (ClientError, json.JSONDecodeError, ValueError):
        telemetry = {}
    for field in (
        "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
        "duration_ms", "num_turns", "confidence", "escalation_reason",
        "raw_input_bytes", "evidence_bytes", "preprocessing_ratio",
        # AI cost-optimization mission Phase 2, Issue 4: cumulative
        # escalation telemetry — see sandbox/entrypoint.py's run_skill.
        "attempt_count",
        "initial_model", "initial_effort", "initial_input_tokens", "initial_output_tokens",
        "initial_cache_read_tokens", "initial_cache_creation_tokens", "initial_duration_ms",
        "initial_estimated_cost_usd",
        "escalation_model", "escalation_effort", "escalation_input_tokens", "escalation_output_tokens",
        "escalation_cache_read_tokens", "escalation_cache_creation_tokens", "escalation_duration_ms",
        "escalation_estimated_cost_usd",
    ):
        if field in telemetry:
            setattr(run, field, telemetry[field])
    if "cost_usd" in telemetry:
        run.estimated_cost_usd = telemetry["cost_usd"]
    if "escalated" in telemetry:
        run.escalated = bool(telemetry["escalated"])

    db.flush()
    return run


@router.post(
    "/incidents/{incident_id}/analysis-runs",
    response_model=AnalysisRunOut,
    status_code=status.HTTP_201_CREATED,
)
def request_analysis(
    incident_id: uuid.UUID,
    body: AnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("incident.analysis.execute")),
) -> AnalysisRunOut:
    incident = _get_incident_or_404(db, incident_id)

    log_evidence = db.scalar(
        select(Evidence)
        .where(Evidence.incident_id == incident.id, Evidence.evidence_type == "LOG")
        .order_by(Evidence.created_at.desc())
    )
    if log_evidence is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Incident has no LOG evidence attached — upload a log before requesting analysis.",
        )

    object_refs = [
        {"bucket": log_evidence.bucket, "key": log_evidence.object_key, "sha256": log_evidence.sha256}
    ]

    # Skill Registry (Reliability mission Batch B): resolves (or creates)
    # the immutable SkillSnapshot for the skill's *current* on-disk
    # content — this is the tamper-evident identity threaded through the
    # cache lookup, the Job/AnalysisRun rows, and the job message the
    # bridge re-verifies before executing.
    # Skill Runtime mission Phase 2: resolve the *active* snapshot, not
    # necessarily whatever is on disk right now — activation genuinely
    # controls what new jobs run.
    skill_snapshot = resolve_active_snapshot(db, SKILL_NAME)
    skill_execution_hash = compute_execution_hash(db, skill_snapshot)

    # AI cost-optimization mission Phase 6: an exact-match cache hit skips
    # RabbitMQ/the bridge/Claude entirely — the Job row is created already
    # COMPLETED and the AnalysisRun copies the cached result verbatim, so
    # the rest of this endpoint's contract (a Job + current AnalysisRun,
    # pollable exactly like a real run) is unchanged for callers.
    cached_run = _find_cached_analysis_run(
        db,
        log_evidence=log_evidence,
        requested_model=body.model,
        requested_effort=body.effort,
        skill_hash=skill_snapshot.content_hash,
        skill_execution_hash=skill_execution_hash,
        skill_snapshot_id=skill_snapshot.id,
    )

    db.execute(update(AnalysisRun).where(AnalysisRun.incident_id == incident.id).values(current=False))

    if cached_run is not None:
        now = datetime.now(timezone.utc)
        provenance = _snapshot_runtime_provenance(skill_snapshot)
        job = Job(
            job_type="log_triage",
            status="COMPLETED",
            incident_id=incident.id,
            requested_by=current_user.id,
            model=cached_run.model,
            effort=cached_run.effort,
            skill_name=SKILL_NAME,
            skill_version=declared_skill_version(skill_snapshot),
            skill_hash=skill_snapshot.content_hash,
            skill_execution_hash=skill_execution_hash,
            skill_snapshot_id=skill_snapshot.id,
            correlation_id=str(uuid.uuid4()),
            started_at=now,
            completed_at=now,
            used_cache=True,
            cache_type="exact",
        )
        db.add(job)
        db.flush()
        run = AnalysisRun(
            incident_id=incident.id,
            job_id=job.id,
            log_evidence_id=log_evidence.id,
            result_json=cached_run.result_json,
            result_text=cached_run.result_text,
            model=cached_run.model,
            effort=cached_run.effort,
            skill_name=SKILL_NAME,
            skill_version=declared_skill_version(skill_snapshot),
            skill_hash=skill_snapshot.content_hash,
            skill_execution_hash=skill_execution_hash,
            skill_snapshot_id=skill_snapshot.id,
            schema_hash=_snapshot_schema_hash(skill_snapshot),
            **provenance,
            cache_contract_version=CACHE_CONTRACT_VERSION,
            input_manifest_sha256=log_evidence.sha256,
            output_sha256=cached_run.output_sha256,
            current=True,
            used_cache=True,
            cache_type="exact",
        )
        db.add(run)
        record_audit(
            db,
            actor_user_id=current_user.id,
            action="incident.analysis.execute",
            resource_type="incident",
            resource_id=str(incident.id),
            metadata={"job_id": str(job.id), "used_cache": True, "cache_type": "exact", "source_run_id": str(cached_run.id)},
        )
        db.commit()
        db.refresh(job)
        db.refresh(run)
        return _to_out(job, run)

    # Reliability mission Batch A: enqueue_job only writes Postgres (a Job
    # row + an OutboxEvent describing the RabbitMQ message) — nothing is
    # published to RabbitMQ here. The outbox dispatcher publishes it only
    # after this whole transaction (Job + AnalysisRun + audit, below)
    # commits, so a message can never reach the bridge for a job whose
    # AnalysisRun doesn't durably exist yet.
    job = enqueue_job(
        db,
        job_type="log_triage",
        requested_by=current_user.id,
        incident_id=str(incident.id),
        object_refs=object_refs,
        model=body.model,
        effort=body.effort,
        skill_name=SKILL_NAME,
        skill_version=declared_skill_version(skill_snapshot),
        skill_hash=skill_snapshot.content_hash,
        skill_execution_hash=skill_execution_hash,
        skill_snapshot_id=skill_snapshot.id,
        ai_policy=execution_policy(skill_snapshot),
    )

    # §22.9: created immediately (not on completion) so input provenance
    # (which evidence, its checksum) is captured against what was
    # actually submitted, not reconstructed later.
    run = AnalysisRun(
        incident_id=incident.id,
        job_id=job.id,
        log_evidence_id=log_evidence.id,
        model=job.model,
        effort=job.effort,
        skill_name=job.skill_name,
        skill_version=job.skill_version,
        skill_hash=job.skill_hash,
        skill_execution_hash=skill_execution_hash,
        skill_snapshot_id=skill_snapshot.id,
        schema_hash=_snapshot_schema_hash(skill_snapshot),
        **_snapshot_runtime_provenance(skill_snapshot),
        cache_contract_version=CACHE_CONTRACT_VERSION,
        input_manifest_sha256=log_evidence.sha256,
        current=True,
    )
    db.add(run)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action="incident.analysis.execute",
        resource_type="incident",
        resource_id=str(incident.id),
        metadata={"job_id": str(job.id)},
    )
    db.commit()
    db.refresh(job)
    db.refresh(run)
    return _to_out(job, run)


@router.get("/incidents/{incident_id}/analysis-runs", response_model=list[AnalysisRunOut])
def list_analysis_runs(
    incident_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> list[AnalysisRunOut]:
    _get_incident_or_404(db, incident_id)

    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.incident_id == incident_id, Job.job_type == "log_triage")
            .order_by(Job.queued_at.desc())
        )
    )
    out = []
    for job in jobs:
        run = _sync_completed_job(db, job)
        out.append(_to_out(job, run))
    db.commit()
    return out


@router.get("/incidents/{incident_id}/analysis-runs/{job_id}", response_model=AnalysisRunOut)
def get_analysis_run(
    incident_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("incident.read")),
) -> AnalysisRunOut:
    _get_incident_or_404(db, incident_id)
    job = db.scalar(
        select(Job).where(Job.id == job_id, Job.incident_id == incident_id, Job.job_type == "log_triage")
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found")

    run = _sync_completed_job(db, job)
    db.commit()
    return _to_out(job, run)
