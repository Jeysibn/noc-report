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
from noc_bridge.storage import ChecksumMismatch, download_object, get_client, upload_artifact
from noc_bridge.validation import OutputValidationError, validate_output

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

logger = logging.getLogger("noc_bridge")

MAX_ATTEMPTS = 1


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

        db.mark_started(pg_conn, job_id)
        publish_status_event(channel, job_id=job_id, event="started", detail={"attempt": attempt})

        with tempfile.TemporaryDirectory(prefix=f"noc-job-{job_id}-input-") as input_dir_s, \
                tempfile.TemporaryDirectory(prefix=f"noc-job-{job_id}-output-") as output_dir_s:
            input_dir = pathlib.Path(input_dir_s)
            output_dir = pathlib.Path(output_dir_s)

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
                skills_dir=self.settings.skills_dir,
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
            validate_output(job_type, result.output)

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
        attempt = payload.get("attempt", 1)

        with self._lock:
            self.active_jobs += 1
        try:
            self._process_job(job_type, payload, pg_conn, minio_client, channel)
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except (ChecksumMismatch, OutputValidationError, UnsupportedJobType, RuntimeError) as exc:
            logger.error("job %s failed (attempt %s): %s", job_id, attempt, exc)
            db.mark_failed(
                pg_conn,
                uuid.UUID(job_id),
                error_code=type(exc).__name__,
                error_message=str(exc)[:2000],
            )
            publish_status_event(
                channel, job_id=job_id, event="failed", detail={"error": str(exc)[:500]}
            )
            if attempt < MAX_ATTEMPTS and not isinstance(exc, UnsupportedJobType):
                # §26 bounded retries: nack without requeue so the queue's
                # DLX wiring routes it to the retry queue's TTL-then-
                # redeliver chain, with the incremented attempt count
                # carried in a freshly-published retry message.
                payload["attempt"] = attempt + 1
                names = queue_names(job_type)
                channel.confirm_delivery()
                channel.basic_publish(
                    exchange="noc.jobs",
                    routing_key=names["routing_key"],
                    body=json.dumps(payload).encode("utf-8"),
                    properties=pika.BasicProperties(
                        delivery_mode=pika.DeliveryMode.Persistent,
                        content_type="application/json",
                    ),
                )
                channel.basic_ack(delivery_tag=method.delivery_tag)
            else:
                # Retries exhausted (or a permanently unsupported job
                # type) — terminal DLQ, per §26 "DLQ after retry
                # exhaustion".
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
