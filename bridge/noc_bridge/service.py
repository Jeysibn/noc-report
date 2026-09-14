"""Claude Bridge main service (master plan §27 Responsibilities 1-18).

Consumes `noc.jobs.*`, loads job metadata, downloads+verifies MinIO
objects, runs an ephemeral Docker sandbox, validates structured output,
uploads artifacts, updates the Job row (Postgres is the source of truth
per §26), publishes status events, cleans up, and ACKs/NACKs.

The sandbox now invokes the real Claude Code CLI (ADR 0003), authenticated
via a mounted copy of the operator's own OAuth credential — never a
separate ANTHROPIC_API_KEY, per explicit operator instruction (the
operator has a Claude Code subscription, not raw API access). See
`noc_bridge.credentials.prepare_sandbox_credentials` and the
`extra_volumes`/`environment`/`network_disabled=False` wiring below.
"""
from __future__ import annotations

import io
import json
import logging
import pathlib
import tempfile
import threading
import time
import uuid
import yaml
from botocore.exceptions import ClientError

import pika

from noc_bridge import db, health
from noc_bridge.config import BridgeSettings, settings as default_settings
from noc_bridge.credentials import prepare_sandbox_credentials
from noc_bridge.failures import (
    EvidenceIntegrityError,
    EvidenceRetrievalError,
    RETRYABLE,
    classify_failure,
)
from noc_bridge.queue_topology import (
    JOB_TYPES,
    declare_topology,
    get_connection,
    publish_status_event,
    queue_names,
    send_to_dlq,
)
from noc_bridge.docx_render import render_daily_report_docx, render_document
from noc_bridge.report_composition import compose_report
from noc_bridge.report_document_json import document_to_preview_json
from noc_bridge.sandbox_runner import SkillJobResult, run_job_sandbox
from noc_bridge.skill_registry import materialize_snapshot, verify_skill_hash
from noc_bridge.storage import ChecksumMismatch, download_object, get_client, object_exists, upload_artifact
from noc_bridge.validation import (
    OutputValidationError,
    validate_output,
    validate_against_schema,
)
from noc_bridge.report_validation import validate_merged_daily_report

# Skill Runtime mission Phase 14: the job message protocol's single source
# of truth — see packages/contracts/job_message.schema.json's own
# docstring. apps/api validates the same file at message-build time
# (app/core/queue.py::build_job_message); the bridge validates every
# incoming delivery against it here before touching any of its keys, so a
# protocol drift between producer and consumer fails loudly and
# immediately instead of as a KeyError deep inside job processing.
_JOB_MESSAGE_SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[2] / "packages" / "contracts" / "job_message.schema.json"
_job_message_schema = json.loads(_JOB_MESSAGE_SCHEMA_PATH.read_text())

# Skill name per job_type. Milestone 14 adds daily_report
# (skills/daily-alert-report/SKILL.md).
_SKILL_NAME_BY_JOB_TYPE = {
    "log_triage": "log-triage-summary",
    "daily_report": "daily-alert-report",
}

# Per job_type: the filename the sandbox expects its one input object
# under (see sandbox/entrypoint.py's _INPUT_FILE_BY_SKILL), and the
# storage bucket a completed job's rendered artifact belongs in.
_INPUT_FILENAME_BY_JOB_TYPE = {"log_triage": "log.txt", "daily_report": "snapshot.json"}

# Skill Runtime mission Phase 13: fixed margin added on top of a job's own
# configured timeout_seconds when computing its claim lease, covering the
# bridge's own pre/post-sandbox work (download, snapshot materialization,
# upload, DB writes) that isn't counted in the sandbox timeout itself.
_LEASE_SAFETY_MARGIN_SECONDS = 120

logger = logging.getLogger("noc_bridge")

# A single sandbox may make two structured-output calls and, when the skill's
# declared quality signal requires it, a second effort-tier call. Reserve all
# four permits once per Job so broker/storage retries cannot multiply paid
# usage. The sandbox itself enforces the same value for its in-process calls.
MAX_PAID_AI_CALLS_PER_JOB = 4

# Reliability mission Batch A/Phase 3: was 1, which made the "retry"
# branch below dead code — every first failure went straight to the DLQ.
# Bounded at 3 real attempts (an initial try plus two retries) before a
# retryable failure is considered exhausted and DLQ'd.
MAX_ATTEMPTS = 3


def _artifact_ref(job_type: str, job_id: uuid.UUID, settings: BridgeSettings) -> tuple[str, str]:
    """The deterministic (bucket, key) a completed job_type's artifact
    lives at — same convention `apps/api`'s `_sync_completed_job`/
    `_sync_completed_report` poll for. Used here to reconcile a
    redelivered message against work a prior attempt already finished."""
    if job_type == "daily_report":
        return settings.minio_bucket_reports, f"reports/{job_id}/report.docx"
    return settings.minio_bucket_job_artifacts, f"jobs/{job_id}/result.json"


def _plan_artifact_ref(job_id: uuid.UUID, settings: BridgeSettings) -> tuple[str, str]:
    """Daily Report Plan artifact used to retry composition without Claude."""
    return settings.minio_bucket_job_artifacts, f"jobs/{job_id}/result.json"


def _merge_daily_report(snapshot: dict, ai_output: dict) -> dict:
    """AI cost-optimization mission Phase 2, Issue 6: deterministically
    rebuilds the full report structure `_validate_daily_report` and
    `render_daily_report_docx` expect, from (a) the frozen snapshot this
    bridge downloaded before ever invoking the sandbox and (b) Claude's
    compact 4-field output (overview_en/zh, cross_incident_findings_en/zh).
    Per-incident fields (title/status/grafana_url/log_filename/
    screenshots/existing analysis) are carried through verbatim — never
    regenerated or round-tripped through Claude."""
    shift_starts_at = snapshot.get("shift_starts_at")
    shift_ends_at = snapshot.get("shift_ends_at")
    title = f"Daily Alert Report — {shift_starts_at} to {shift_ends_at}"

    sections = [
        {
            "incident_display_id": incident["display_id"],
            "title": incident["title"],
            "status": incident["status"],
            "grafana_url": incident.get("grafana_url"),
            "log_filename": incident.get("log_filename"),
            "screenshots": incident.get("screenshots") or [],
            "analysis": incident.get("analysis"),
        }
        for incident in snapshot.get("incidents", [])
    ]

    return {
        "title": title,
        "overview_en": ai_output["overview_en"],
        "overview_zh": ai_output["overview_zh"],
        "cross_incident_findings_en": ai_output["cross_incident_findings_en"],
        "cross_incident_findings_zh": ai_output["cross_incident_findings_zh"],
        "sections": sections,
    }


class UnsupportedJobType(RuntimeError):
    """Raised for a job_type with no real skill wired yet — a deliberate,
    honest failure rather than fabricating a result."""


class BridgeService:
    def __init__(self, settings: BridgeSettings | None = None):
        self.settings = settings or default_settings
        self.active_jobs = 0
        self._lock = threading.Lock()
        # Resolved once per service lifetime (not per job): the binary
        # path is a symlink on the host (~/.local/bin/claude ->
        # .../versions/2.1.265) that must be followed to a real file
        # before bind-mounting, since a bind-mounted symlink would dangle
        # inside the container (its host-absolute target doesn't exist
        # there). The credential copy is likewise prepared once; it's a
        # short-lived OAuth token file, and re-copying it per job buys
        # nothing since the source only changes on the operator's own
        # `claude` re-login.
        self._claude_binary_real_path = self.settings.claude_binary_path.resolve()
        self._claude_creds_dir = None
        # Reliability mission Batch A (idempotent job lifecycle): identifies
        # this process as the claimant on Job.worker_id/claim_token, purely
        # for observability/debugging (claim correctness itself doesn't
        # depend on this being globally unique across restarts, only on the
        # lease-expiry check in noc_bridge.db.claim_job).
        self.worker_id = f"{uuid.uuid4()}"
        # Milestone 17 gap follow-up (AI Configuration): last prefetch_count
        # applied via basic_qos, so it's only re-set when system_config's
        # max_concurrent_jobs actually changes, not on every single job.
        self._last_max_concurrency: int | None = None

    def _ensure_claude_creds_dir(self) -> pathlib.Path:
        if self._claude_creds_dir is None:
            self._claude_creds_dir = prepare_sandbox_credentials(
                self.settings.claude_credentials_path
            )
        return self._claude_creds_dir

    # -- job processing -----------------------------------------------

    def _lease_heartbeat(self, job_id: uuid.UUID, claim_token: str, lease_seconds: int, stop: threading.Event) -> None:
        """Renew the claim while pre-processing, sandbox execution, and
        artifact upload are in flight. The heartbeat uses its own DB
        connection because the consumer connection is also used for status
        commits and must never be concurrently used by two threads."""
        conn = None
        interval = max(1.0, min(30.0, lease_seconds / 3))
        try:
            conn = db.get_connection(self.settings.database_url)
            while not stop.wait(interval):
                try:
                    if not db.renew_job_lease(conn, job_id, claim_token, lease_seconds=lease_seconds):
                        logger.warning("lease renewal rejected for job %s; claim is no longer owned", job_id)
                        return
                except Exception:
                    logger.warning("lease renewal failed for job %s", job_id, exc_info=True)
        finally:
            if conn is not None:
                conn.close()

    def _process_job(self, job_type: str, payload: dict, pg_conn, minio_client, channel) -> None:
        job_id = uuid.UUID(payload["job_id"])
        attempt = payload.get("attempt", 1)

        # Milestone 17 gap follow-up (AI Configuration): read fresh on every
        # dispatch, not cached at startup, so an admin's PATCH via
        # GET/PATCH /admin/system-config takes effect on the very next job.
        config = db.load_system_config(pg_conn)
        if config["max_concurrent_jobs"] != self._last_max_concurrency:
            channel.basic_qos(prefetch_count=config["max_concurrent_jobs"])
            self._last_max_concurrency = config["max_concurrent_jobs"]

        # Skill Runtime mission Phase 1: SkillSnapshot is execution truth,
        # not "whatever is currently on disk." A job stamped with a
        # skill_hash at enqueue time must execute the exact immutable
        # snapshot content that hash identifies — even if the skill was
        # edited or a newer version activated after this job was queued.
        # Falls back to the old hash-verify-against-live-disk behavior
        # only when the message carries no skill_hash at all (an older
        # enqueuer/test that never went through the Skill Registry).
        skill_name = payload.get("skill_name") or _SKILL_NAME_BY_JOB_TYPE.get(job_type)
        skill_hash = payload.get("skill_hash") if skill_name else None
        skill_execution_hash = payload.get("skill_execution_hash") if skill_name else None
        skill_snapshot_id = payload.get("skill_snapshot_id") if skill_name else None
        if job_type in _SKILL_NAME_BY_JOB_TYPE and skill_hash is None:
            # No skill_hash at all (older enqueuer/test): nothing to
            # materialize against, so fall back to the live mutable
            # checkout with a no-op verify (verify_skill_hash no-ops when
            # expected_hash is None).
            verify_skill_hash(
                skill_name,
                skill_hash,
                skills_dir=self.settings.skills_dir,
            )

        # Reliability mission Batch A: reconcile before claiming/executing
        # anything. If a prior attempt already produced this job's
        # deterministic artifact (crashed after upload but before
        # mark_completed/ACK), don't re-run Claude at all — just finalize
        # from what's already there.
        artifact_bucket, artifact_key = _artifact_ref(job_type, job_id, self.settings)
        if object_exists(minio_client, bucket=artifact_bucket, object_key=artifact_key):
            logger.info("job %s artifact already present at %s/%s — reconciling without re-executing", job_id, artifact_bucket, artifact_key)
            db.mark_completed(pg_conn, job_id)
            publish_status_event(channel, job_id=job_id, event="completed", detail={"artifact_key": artifact_key, "reconciled": True})
            return

        publish_status_event(channel, job_id=job_id, event="started", detail={"attempt": attempt})

        with tempfile.TemporaryDirectory(prefix=f"noc-job-{job_id}-input-") as input_dir_s, \
                tempfile.TemporaryDirectory(prefix=f"noc-job-{job_id}-output-") as output_dir_s, \
                tempfile.TemporaryDirectory(prefix=f"noc-job-{job_id}-skill-") as skill_dir_s:
            input_dir = pathlib.Path(input_dir_s)
            output_dir = pathlib.Path(output_dir_s)

            # Skill Runtime mission Phase 1: materialize the exact
            # immutable SkillSnapshot content this job was stamped with at
            # enqueue time — never the live, possibly-since-edited
            # `skills/` checkout — into a per-job scratch directory, and
            # mount *that* into the sandbox. This is what guarantees "Job A
            # created against Skill v3 still runs Skill v3 even if v4 was
            # activated and the files rewritten before this worker ever
            # dequeued the message." Raises SkillSnapshotMissing (terminal)
            # if the DB row backing this hash is gone.
            job_skills_dir = self.settings.skills_dir
            if skill_name and (skill_snapshot_id is not None or skill_hash is not None):
                job_skills_dir = materialize_snapshot(
                    pg_conn,
                    skill_name,
                    skill_hash,
                    snapshot_id=skill_snapshot_id,
                    execution_hash=skill_execution_hash,
                    dest_root=pathlib.Path(skill_dir_s),
                )

            input_filename = None
            if skill_name and job_skills_dir != self.settings.skills_dir:
                try:
                    manifest = yaml.safe_load((job_skills_dir / skill_name / "skill.yaml").read_text()) or {}
                    contract = manifest.get("input_contract", {})
                    input_filename = contract if isinstance(contract, str) else contract.get("filename")
                except (OSError, yaml.YAMLError, AttributeError) as exc:
                    raise RuntimeError(f"invalid input contract for {skill_name}: {exc}") from exc
            if input_filename is None:
                input_filename = _INPUT_FILENAME_BY_JOB_TYPE.get(job_type)
            if input_filename is None:
                raise UnsupportedJobType(f"no skill wired yet for job_type={job_type!r}")

            # §27 steps 5-6: download + checksum-verify every referenced
            # object before the sandbox ever sees it. Both job types
            # currently carry exactly one input object.
            for ref in payload["object_refs"]:
                download_object(
                    minio_client,
                    bucket=ref["bucket"],
                    object_key=ref["key"],
                    dest_path=input_dir / input_filename,
                    expected_sha256=ref.get("sha256"),
                )

            publish_status_event(channel, job_id=job_id, event="progress", detail={"stage": "sandbox"})

            # A daily report stores the validated ReportPlan separately from
            # the final DOCX. If composition/upload fails after Claude has
            # succeeded, a retry reuses this durable plan and spends zero new
            # paid calls. This is the report equivalent of result-artifact
            # reconciliation for analysis jobs.
            reusable_plan = None
            if job_type == "daily_report":
                plan_bucket, plan_key = _plan_artifact_ref(job_id, self.settings)
                if object_exists(minio_client, bucket=plan_bucket, object_key=plan_key):
                    plan_path = output_dir / "result.json"
                    download_object(minio_client, bucket=plan_bucket, object_key=plan_key, dest_path=plan_path)
                    reusable_plan = json.loads(plan_path.read_text())

            if reusable_plan is not None:
                result = SkillJobResult(exit_code=0, output=reusable_plan, logs="reused durable ReportPlan")
            else:
                creds_dir = self._ensure_claude_creds_dir()
                if not skill_name:
                    raise UnsupportedJobType(f"no skill wired yet for job_type={job_type!r}")

                paid_ai_budget = db.reserve_paid_ai_calls(
                    pg_conn, job_id, requested=MAX_PAID_AI_CALLS_PER_JOB
                )
                if paid_ai_budget <= 0:
                    raise RuntimeError("paid AI retry budget exhausted")

                result = run_job_sandbox(
                input_dir=input_dir,
                output_dir=output_dir,
                skills_dir=job_skills_dir,
                # Milestone 17 gap follow-up: job_timeout_seconds now comes
                # from system_config, not the static BRIDGE_SANDBOX_TIMEOUT_
                # SECONDS env var — an admin can tighten/loosen it live.
                timeout_seconds=config["job_timeout_seconds"],
                cpu_nano_cpus=self.settings.sandbox_cpu_nano_cpus,
                mem_limit=self.settings.sandbox_mem_limit,
                pid_limit=self.settings.sandbox_pid_limit,
                # ADR 0003: the real `claude` CLI needs network egress to
                # reach Anthropic, and needs its binary + OAuth credential
                # bind-mounted in. Every other §28 constraint (non-root,
                # cap-drop=ALL, no-new-privileges, read-only rootfs,
                # tmpfs-only writes, resource/PID/time limits, force
                # removal) is unchanged.
                network_disabled=False,
                extra_volumes={
                    str(self._claude_binary_real_path): {
                        "bind": "/usr/local/bin/claude",
                        "mode": "ro",
                    },
                    str(creds_dir): {
                        # The bridge keeps the host UID inside the container
                        # so private bind-mounted files remain readable only
                        # by that UID. The image's /home/sandbox directory is
                        # owned by UID 10001 and intentionally 0700, so it is
                        # not traversable after that user override. Put the
                        # isolated read-only credential directory under the
                        # sandbox tmpfs instead and point HOME there.
                        "bind": "/tmp/claude-home/.claude",
                        "mode": "ro",
                    },
                },
                environment={
                    "HOME": "/tmp/claude-home",
                    "SKILL_NAME": skill_name,
                    # A job's own requested model/effort (payload, set at
                    # request time in apps/api) always wins; system_config
                    # only supplies the fallback default, same precedent as
                    # the pre-existing claude_model_default fallback.
                    "SKILL_MODEL": payload.get("model") or config["default_model"],
                    "SKILL_EFFORT": payload.get("effort") or config["default_effort"],
                    "SKILL_EFFORT_ESCALATION": self.settings.claude_effort_escalation,
                    "SKILL_ESCALATION_CONFIDENCE_THRESHOLD": str(
                        self.settings.claude_escalation_confidence_threshold
                    ),
                    "SKILL_MAX_BUDGET_USD": str(self.settings.claude_max_budget_usd),
                    "SKILL_CLI_TIMEOUT_SECONDS": str(self.settings.claude_cli_timeout_seconds),
                    "SKILL_MAX_LOG_CHARS": str(self.settings.skill_max_log_chars),
                    "SKILL_MAX_PATTERN_GROUPS": str(self.settings.skill_max_pattern_groups),
                    "SKILL_AI_CALL_BUDGET": str(paid_ai_budget),
                },
                )

                # Persist actual completed Claude calls separately from the
                # conservative pre-launch reservation. A downstream retry
                # may use the remaining paid budget, while a crashed worker
                # still leaves its reservation as a safety fence.
                if result.telemetry is not None:
                    db.record_paid_ai_calls(
                        pg_conn,
                        job_id,
                        result.telemetry.get("claude_calls", 0),
                    )

            if result.exit_code != 0:
                raise RuntimeError(f"sandbox exited {result.exit_code}: {result.logs[-2000:]}")

            # §27 step 13: structured output validation, before upload.
            # For daily_report this now validates the COMPACT AI output
            # (see noc_bridge.validation._validate_daily_report_ai_output)
            # — the full report is assembled and validated separately
            # right below, once merged with the frozen snapshot.
            validate_output(job_type, result.output, skill_name=skill_name, skills_dir=job_skills_dir)

            if job_type == "daily_report" and reusable_plan is None:
                # Preserve the AI result before deterministic composition.
                # A later MinIO/DOCX/evidence failure can therefore retry
                # composition without re-invoking Claude.
                upload_artifact(
                    minio_client,
                    bucket=self.settings.minio_bucket_job_artifacts,
                    object_key=f"jobs/{job_id}/result.json",
                    src_path=output_dir / "result.json",
                )

            renderer_profile = None
            try:
                manifest = yaml.safe_load((job_skills_dir / skill_name / "skill.yaml").read_text()) or {}
                renderer_profile = manifest.get("renderer_profile")
            except (OSError, yaml.YAMLError) as exc:
                raise RuntimeError(f"could not load renderer profile for {skill_name}: {exc}") from exc

            # §27 step 14: select exactly one artifact path from the
            # snapshot's renderer profile.  A declarative ReportDocument
            # result must not fall through to the legacy daily-report
            # assembler after it has already been accepted by the generic
            # schema and adapter.
            if job_type == "daily_report":
                docx_path = output_dir / "report.docx"
                if renderer_profile == "report-document-v1":
                    snapshot = json.loads((input_dir / "snapshot.json").read_text())
                    allowed_screenshots = {
                        (str(item.get("bucket")), str(item.get("object_key")))
                        for incident in snapshot.get("incidents", [])
                        for item in (incident.get("screenshots") or [])
                        if item.get("bucket") and item.get("object_key")
                    }

                    def _fetch_trusted_screenshot(bucket: str, object_key: str, _client=minio_client) -> bytes | None:
                        # The composition module creates screenshot blocks only
                        # from this frozen allow-list. Keep this second guard
                        # at the storage boundary so a future renderer call
                        # cannot turn model output into an arbitrary MinIO
                        # read.
                        if (bucket, object_key) not in allowed_screenshots:
                            logger.error("rejected non-frozen screenshot reference %s/%s", bucket, object_key)
                            raise OutputValidationError(
                                f"screenshot reference is outside the frozen report snapshot: {bucket}/{object_key}"
                            )
                        try:
                            buf = io.BytesIO()
                            _client.download_fileobj(bucket, object_key, buf)
                            return buf.getvalue()
                        except ClientError as exc:
                            code = str((exc.response or {}).get("Error", {}).get("Code", ""))
                            if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
                                raise EvidenceIntegrityError(
                                    f"frozen screenshot object is missing: {bucket}/{object_key}"
                                ) from exc
                            raise EvidenceRetrievalError(
                                f"temporary screenshot retrieval failure: {bucket}/{object_key}"
                            ) from exc
                        except EvidenceIntegrityError:
                            raise
                        except Exception as exc:
                            logger.warning("could not fetch trusted screenshot %s/%s for report", bucket, object_key)
                            raise EvidenceRetrievalError(
                                f"temporary screenshot retrieval failure: {bucket}/{object_key}"
                            ) from exc

                    document = compose_report(result.output, snapshot)
                    render_document(
                        document,
                        docx_path,
                        screenshot_fetcher=_fetch_trusted_screenshot,
                    )

                    # Web/DOCX consistency: the web Report Builder previews
                    # the exact same composed ReportDocument the DOCX was
                    # rendered from, not a hand-maintained second layout.
                    # Screenshots are split into a private index (real
                    # bucket/object_key, for the API server only, to proxy
                    # bytes back to an <img> tag) and never embedded in the
                    # browser-facing preview payload itself.
                    preview_payload, screenshot_index = document_to_preview_json(document)
                    preview_path = output_dir / "document.json"
                    preview_path.write_text(json.dumps(preview_payload))
                    screenshot_index_path = output_dir / "document.screenshots.json"
                    screenshot_index_path.write_text(json.dumps(screenshot_index))
                    upload_artifact(
                        minio_client,
                        bucket=self.settings.minio_bucket_reports,
                        object_key=f"reports/{job_id}/document.json",
                        src_path=preview_path,
                    )
                    upload_artifact(
                        minio_client,
                        bucket=self.settings.minio_bucket_reports,
                        object_key=f"reports/{job_id}/document.screenshots.json",
                        src_path=screenshot_index_path,
                    )
                elif renderer_profile == "daily_report_docx":
                    # The current daily-report skill returns a compact
                    # AI-owned shape. Deterministic incident data is merged
                    # by its compatibility assembler before rendering.
                    snapshot = json.loads((input_dir / "snapshot.json").read_text())
                    result.output = _merge_daily_report(snapshot, result.output)
                    validate_merged_daily_report(result.output)

                    def _fetch_screenshot(bucket: str, object_key: str, _client=minio_client) -> bytes | None:
                        try:
                            buf = io.BytesIO()
                            _client.download_fileobj(bucket, object_key, buf)
                            return buf.getvalue()
                        except ClientError as exc:
                            code = str((exc.response or {}).get("Error", {}).get("Code", ""))
                            if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
                                raise EvidenceIntegrityError(
                                    f"frozen screenshot object is missing: {bucket}/{object_key}"
                                ) from exc
                            raise EvidenceRetrievalError(
                                f"temporary screenshot retrieval failure: {bucket}/{object_key}"
                            ) from exc
                        except Exception as exc:
                            logger.warning("could not fetch screenshot %s/%s for report", bucket, object_key)
                            raise EvidenceRetrievalError(
                                f"temporary screenshot retrieval failure: {bucket}/{object_key}"
                            ) from exc

                    render_daily_report_docx(result.output, docx_path, screenshot_fetcher=_fetch_screenshot)
                else:
                    raise RuntimeError(f"unsupported renderer profile: {renderer_profile!r}")

                object_key = f"reports/{job_id}/report.docx"
                upload_artifact(
                    minio_client,
                    bucket=self.settings.minio_bucket_reports,
                    object_key=object_key,
                    src_path=docx_path,
                )
            else:
                result_path = output_dir / "result.json"
                object_key = f"jobs/{job_id}/result.json"
                upload_artifact(
                    minio_client,
                    bucket=self.settings.minio_bucket_job_artifacts,
                    object_key=object_key,
                    src_path=result_path,
                )

            # Phase 1 (AI usage telemetry): sandbox/entrypoint.py writes
            # telemetry.json alongside result.json whenever it ran Claude
            # at all — best-effort only, so a missing file (older sandbox
            # image, or the entrypoint's own defensive write failure)
            # never fails an otherwise-successful job. apps/api picks this
            # up the same way it picks up result.json (poll + sync).
            telemetry_path = output_dir / "telemetry.json"
            if telemetry_path.exists():
                try:
                    upload_artifact(
                        minio_client,
                        bucket=self.settings.minio_bucket_job_artifacts,
                        object_key=f"jobs/{job_id}/telemetry.json",
                        src_path=telemetry_path,
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.warning("failed to upload telemetry.json for job %s", job_id, exc_info=True)

        db.mark_completed(pg_conn, job_id)
        publish_status_event(
            channel,
            job_id=job_id,
            event="completed",
            detail={"artifact_key": object_key},
        )

    def _handle_delivery(self, channel, method, properties, body, pg_conn, minio_client, job_type: str) -> None:
        payload = json.loads(body)

        # Skill Runtime mission Phase 14: reject any message that doesn't
        # conform to the canonical job message protocol before touching any
        # of its keys. A poison-pill/drifted message is routed straight to
        # this job_type's DLQ and acked, same as any other terminal
        # failure — never left to crash the whole consumer loop as an
        # uncaught exception (there's no reliable job_id to update in
        # Postgres for a message this malformed).
        try:
            validate_against_schema(payload, _job_message_schema, label="job_message")
        except OutputValidationError as exc:
            logger.error("job message failed protocol validation, routing to DLQ: %s", exc)
            send_to_dlq(channel, job_type=job_type, body=payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        if payload.get("job_type") != job_type:
            logger.error("job message type %r arrived on %r queue", payload.get("job_type"), job_type)
            send_to_dlq(channel, job_type=job_type, body=payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        job_id = payload["job_id"]
        job_uuid = uuid.UUID(job_id)

        # Idempotent job lifecycle (Reliability mission Batch A/Phase 2):
        # RabbitMQ delivery is at-least-once, so this same message can
        # arrive more than once. Before doing anything else:
        #   - a job already COMPLETED is a pure redelivery of finished
        #     work — ack and do nothing.
        #   - claim the job otherwise; a claim can fail only if some other
        #     delivery holds a still-live lease on it, in which case this
        #     delivery is redundant right now and is safely ack'd without
        #     executing (the other delivery owns finishing it).
        existing = db.fetch_job_row(pg_conn, job_uuid)
        if existing is None:
            logger.error("job %s is not present in Postgres; routing to DLQ", job_id)
            send_to_dlq(channel, job_type=job_type, body=payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        # The database row is authoritative for an already-created job.
        # Reject a message whose immutable execution reference was altered in
        # transit or by an operator; never execute a different snapshot.
        if existing.get("skill_snapshot_id") is not None and payload.get("skill_snapshot_id") != str(existing["skill_snapshot_id"]):
            logger.error("job %s snapshot reference does not match its database row", job_id)
            send_to_dlq(channel, job_type=job_type, body=payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        if existing.get("skill_snapshot_id") is None and payload.get("skill_snapshot_id") is not None:
            logger.error("job %s message adds a snapshot reference absent from its database row", job_id)
            send_to_dlq(channel, job_type=job_type, body=payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        if existing.get("skill_hash") is not None and payload.get("skill_hash") != existing["skill_hash"]:
            logger.error("job %s skill hash does not match its database row", job_id)
            send_to_dlq(channel, job_type=job_type, body=payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        if existing["status"] == "COMPLETED":
            logger.info("job %s redelivered but already COMPLETED — ack without re-executing", job_id)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        # Skill Runtime mission Phase 13: the lease must outlive the job's
        # own configured timeout, or a long-but-legitimately-still-running
        # job's lease can expire mid-execution and be reclaimed (by a
        # redelivery of the same message, or a second worker), causing it
        # to run twice concurrently. `db.claim_job`'s old fixed 600s
        # default was shorter than daily-alert-report/log-triage-summary's
        # own 900s skill.yaml timeout_seconds — a job that legitimately
        # took longer than 600s (but less than its real 900s budget) could
        # already have had its lease reclaimed here. Read the same
        # system_config value _process_job passes to run_job_sandbox so
        # the lease is always at least as long as the sandbox is allowed
        # to run, plus a fixed safety margin for the bridge's own
        # pre/post-sandbox work (download, upload, DB writes).
        lease_seconds = db.load_system_config(pg_conn)["job_timeout_seconds"] + _LEASE_SAFETY_MARGIN_SECONDS
        claimed = db.claim_job(pg_conn, job_uuid, worker_id=self.worker_id, lease_seconds=lease_seconds)
        if claimed is None:
            logger.info("job %s could not be claimed (already leased elsewhere) — ack without re-executing", job_id)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        attempt = claimed["attempt"]  # Postgres is authoritative for attempt count, not the message payload
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._lease_heartbeat,
            args=(job_uuid, claimed["claim_token"], lease_seconds, heartbeat_stop),
            name=f"lease-heartbeat-{job_uuid}",
            daemon=True,
        )
        heartbeat.start()

        with self._lock:
            self.active_jobs += 1
        try:
            self._process_job(job_type, payload, pg_conn, minio_client, channel)
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except (ChecksumMismatch, OutputValidationError, UnsupportedJobType, RuntimeError) as exc:
            outcome = classify_failure(exc)
            logger.error("job %s failed (attempt %s, %s): %s", job_id, attempt, outcome, exc)
            db.mark_failed(
                pg_conn,
                job_uuid,
                error_code=type(exc).__name__,
                error_message=str(exc)[:2000],
            )
            publish_status_event(
                channel, job_id=job_id, event="failed", detail={"error": str(exc)[:500], "classification": outcome}
            )
            next_attempt = attempt + 1
            if outcome == RETRYABLE and next_attempt <= MAX_ATTEMPTS:
                # §26 bounded retries: the authoritative attempt count now
                # lives on the Job row (bumped here), not in the message
                # body — nacking without requeue lets the main queue's own
                # dead-letter wiring (declared in queue_topology.py) route
                # this message through the retry queue's TTL and back onto
                # the main queue for redelivery, rather than this consumer
                # hand-republishing a lookalike message itself.
                db.bump_attempt(pg_conn, job_uuid, next_attempt)
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            else:
                # Retries exhausted, or a terminal failure — DLQ, per §26
                # "DLQ after retry exhaustion".
                send_to_dlq(channel, job_type=job_type, body=payload)
                channel.basic_ack(delivery_tag=method.delivery_tag)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)
            with self._lock:
                self.active_jobs -= 1

    # -- consumer loop --------------------------------------------------

    def run_forever(self) -> None:
        """Reconnects on any connection-level failure instead of letting one
        kill the whole process. A job's sandbox lifecycle runs synchronously
        inside pika's callback (see queue_topology.get_connection's comment
        on CONNECTION_HEARTBEAT_SECONDS), so a heartbeat-starved or otherwise
        dropped connection is a real, observed failure mode (a
        StreamLostError from a long-running job took down the entire bridge
        with a job stuck at PROCESSING forever, requiring a manual restart)
        — every job in flight when that happens is left unacked and gets
        redelivered once RabbitMQ notices the consumer is gone, so
        reconnecting here is safe rather than silently dropping work."""
        health_server = health.start_health_server(self)
        backoff_seconds = 1
        try:
            while True:
                try:
                    connection = get_connection(self.settings.rabbitmq_url)
                    channel = connection.channel()
                    declare_topology(channel)
                    channel.basic_qos(prefetch_count=self.settings.max_concurrency)

                    pg_conn = db.get_connection(self.settings.database_url)
                    minio_client = get_client(self.settings)

                    for job_type in JOB_TYPES:
                        names = queue_names(job_type)

                        def _callback(ch, method, properties, body, _job_type=job_type):
                            self._handle_delivery(ch, method, properties, body, pg_conn, minio_client, _job_type)

                        channel.basic_consume(queue=names["main"], on_message_callback=_callback)

                    logger.info(
                        "noc-claude-bridge consuming: %s", [queue_names(jt)["main"] for jt in JOB_TYPES]
                    )
                    backoff_seconds = 1  # reset once a connection is actually established
                    channel.start_consuming()
                except (pika.exceptions.AMQPError, OSError) as exc:
                    logger.error(
                        "bridge connection lost (%s) — reconnecting in %ss", exc, backoff_seconds
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, 60)
        finally:
            health_server.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    BridgeService().run_forever()


if __name__ == "__main__":
    main()
