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

import pika

from noc_bridge import db, health
from noc_bridge.config import BridgeSettings, settings as default_settings
from noc_bridge.credentials import prepare_sandbox_credentials
from noc_bridge.failures import RETRYABLE, classify_failure
from noc_bridge.queue_topology import (
    JOB_TYPES,
    declare_topology,
    get_connection,
    publish_status_event,
    queue_names,
    send_to_dlq,
)
from noc_bridge.docx_render import render_daily_report_docx
from noc_bridge.sandbox_runner import run_job_sandbox
from noc_bridge.skill_registry import SkillHashMismatch, SkillSnapshotMissing, materialize_snapshot, verify_skill_hash
from noc_bridge.storage import ChecksumMismatch, download_object, get_client, object_exists, upload_artifact
from noc_bridge.validation import OutputValidationError, validate_output, validate_merged_daily_report

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
        skill_hash = payload.get("skill_hash") if job_type in _SKILL_NAME_BY_JOB_TYPE else None
        if job_type in _SKILL_NAME_BY_JOB_TYPE and skill_hash is None:
            # No skill_hash at all (older enqueuer/test): nothing to
            # materialize against, so fall back to the live mutable
            # checkout with a no-op verify (verify_skill_hash no-ops when
            # expected_hash is None).
            verify_skill_hash(
                _SKILL_NAME_BY_JOB_TYPE[job_type],
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
            if job_type in _SKILL_NAME_BY_JOB_TYPE and skill_hash is not None:
                job_skills_dir = materialize_snapshot(
                    pg_conn,
                    _SKILL_NAME_BY_JOB_TYPE[job_type],
                    skill_hash,
                    dest_root=pathlib.Path(skill_dir_s),
                )

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

            creds_dir = self._ensure_claude_creds_dir()
            skill_name = _SKILL_NAME_BY_JOB_TYPE[job_type]

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
                        "bind": "/home/sandbox/.claude",
                        "mode": "ro",
                    },
                },
                environment={
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
                    "HOME": "/home/sandbox",
                },
            )

            if result.exit_code != 0:
                raise RuntimeError(f"sandbox exited {result.exit_code}: {result.logs[-2000:]}")

            # §27 step 13: structured output validation, before upload.
            # For daily_report this now validates the COMPACT AI output
            # (see noc_bridge.validation._validate_daily_report_ai_output)
            # — the full report is assembled and validated separately
            # right below, once merged with the frozen snapshot.
            validate_output(job_type, result.output, skill_name=skill_name, skills_dir=job_skills_dir)

            if job_type == "daily_report":
                # AI cost-optimization mission Phase 2, Issue 6: Claude
                # only ever saw/produced the compact 4-field output
                # validated above. Everything else in the final report —
                # title, per-incident sections (status/grafana_url/
                # log_filename/screenshots/existing analysis) — is carried
                # through verbatim from the frozen snapshot this bridge
                # already downloaded to input_dir before invoking the
                # sandbox, never regenerated or round-tripped through
                # Claude. This is the deterministic-assembly step the
                # mission requires: "Deterministic code computes facts.
                # Claude interprets evidence."
                snapshot = json.loads((input_dir / "snapshot.json").read_text())
                result.output = _merge_daily_report(snapshot, result.output)
                validate_merged_daily_report(result.output)

            # §27 step 14: upload the validated result. log_triage's
            # result *is* the artifact (raw JSON, into
            # noc-job-artifacts); daily_report's validated JSON gets
            # rendered to DOCX first (master plan §29: "... ->
            # daily-alert-report -> DOCX -> MinIO -> UI download") and
            # that DOCX is the artifact, uploaded into noc-reports at the
            # deterministic key app/api/v1/routers/reports.py polls for.
            if job_type == "daily_report":
                docx_path = output_dir / "report.docx"

                def _fetch_screenshot(bucket: str, object_key: str, _client=minio_client) -> bytes | None:
                    try:
                        buf = io.BytesIO()
                        _client.download_fileobj(bucket, object_key, buf)
                        return buf.getvalue()
                    except Exception:
                        logger.warning("could not fetch screenshot %s/%s for report", bucket, object_key)
                        return None

                render_daily_report_docx(result.output, docx_path, screenshot_fetcher=_fetch_screenshot)
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
        if existing is not None and existing["status"] == "COMPLETED":
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
