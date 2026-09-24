"""RabbitMQ AI Worker for Phase 1 Hermes log analysis.

This process owns the application-side job lifecycle and storage access. It
does not implement an agent loop or provider SDK; Hermes does that behind its
authenticated HTTP boundary.
"""
from __future__ import annotations

import json
import hashlib
import logging
import pathlib
import tempfile
import threading
import time
import uuid

import jsonschema

from noc_bridge.db import (
    JobClaimDisposition,
    bump_attempt,
    claim_job,
    get_connection as get_db_connection,
    mark_completed,
    mark_failed,
    mark_retrying,
    renew_job_lease,
)
from noc_bridge.failures import TERMINAL, classify_failure
from noc_bridge.failures import EvidenceIntegrityError
from noc_bridge.hermes import HermesClient, HermesInvalidResponse
from noc_bridge.daily_report import (
    build_daily_report_input,
    compose_and_render,
    existing_report_artifacts,
    load_saved_plan,
    report_object_keys,
)
from noc_bridge.queue_topology import (
    DLX_EXCHANGE,
    JOBS_EXCHANGE,
    declare_topology,
    get_connection as get_rabbit_connection,
    queue_names,
    send_raw_to_dlq,
    send_to_dlq,
)
from noc_bridge.skill_registry import materialize_snapshot
from noc_bridge.storage import (
    ChecksumMismatch,
    download_object,
    get_client,
    object_exists,
    upload_artifact,
    upload_artifact_metadata,
)
from noc_bridge.validation import (
    OutputValidationError,
    validate_against_schema,
    validate_daily_report_result,
    validate_log_triage_result,
)
from noc_bridge.worker_health import (
    WorkerMetrics,
    hermes_health_url,
    hermes_is_reachable,
    start_health_server,
)
from preprocessing import (
    build_hermes_input,
    compact_log_triage_narrative,
    preprocess_log,
    reconcile_result,
)


LOG = logging.getLogger("noc.ai_worker")
_ROOT = pathlib.Path(__file__).resolve().parents[2]
_JOB_SCHEMA = json.loads((_ROOT / "packages" / "contracts" / "job_message.schema.json").read_text())


class WorkerInputError(RuntimeError):
    retryable = False
    error_code = "WORKER_INPUT_INVALID"


class EvidenceVerificationError(WorkerInputError):
    error_code = "EVIDENCE_VERIFICATION_FAILED"


class SkillConfigurationError(WorkerInputError):
    error_code = "SKILL_CONFIGURATION_FAILED"


class JobLeaseError(RuntimeError):
    retryable = True
    error_code = "JOB_LEASE_LOST"


class _LeaseRenewer:
    """Keep a claimed job lease alive while Hermes performs inference."""

    def __init__(self, settings, job_id: uuid.UUID, claim_token: str):
        self.settings = settings
        self.job_id = job_id
        self.claim_token = claim_token
        self._stop = threading.Event()
        self.lost = False
        self._thread = threading.Thread(target=self._run, name=f"lease-{job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(1.0, min(60.0, self.settings.lease_seconds / 3))
        while not self._stop.wait(interval):
            conn = None
            try:
                conn = get_db_connection(self.settings.database_url)
                if not renew_job_lease(
                    conn,
                    self.job_id,
                    self.claim_token,
                    lease_seconds=self.settings.lease_seconds,
                ):
                    self.lost = True
                    LOG.error("job lease lost job_id=%s", self.job_id)
                    return
            except Exception as exc:
                LOG.error("job lease renewal failed job_id=%s error_type=%s", self.job_id, type(exc).__name__)
            finally:
                if conn is not None:
                    conn.close()


def _validate_job_message(message: dict) -> None:
    jsonschema.validate(message, _JOB_SCHEMA, format_checker=jsonschema.FormatChecker())


def _incident_and_evidence(conn, message: dict) -> tuple[dict, dict]:
    incident_id = message.get("incident_id")
    refs = message.get("object_refs") or []
    if not incident_id or len(refs) != 1:
        raise EvidenceVerificationError("log analysis requires one incident and one evidence object")
    ref = refs[0]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, service, environment, triggered_at, recovered_at FROM incidents WHERE id = %s",
            (str(incident_id),),
        )
        incident = cur.fetchone()
        cur.execute(
            "SELECT id, bucket, object_key, original_filename, sha256, version_id, byte_size "
            "FROM evidence WHERE incident_id = %s AND evidence_type = 'LOG' AND lifecycle_state = 'ACTIVE' "
            "AND bucket = %s AND object_key = %s ORDER BY created_at DESC LIMIT 1",
            (str(incident_id), ref["bucket"], ref["key"]),
        )
        evidence = cur.fetchone()
    if incident is None or evidence is None:
        raise EvidenceVerificationError("job evidence is no longer attached to the incident")
    if ref.get("sha256") and evidence[4] != ref["sha256"]:
        raise EvidenceVerificationError("job evidence checksum does not match application metadata")
    if ref.get("version_id") and evidence[5] != ref["version_id"]:
        raise EvidenceVerificationError("job evidence version does not match application metadata")
    return (
        {
            "id": str(incident[0]),
            "title": incident[1],
            "service": incident[2],
            "environment": incident[3],
            "triggered_at": incident[4].isoformat() if incident[4] else None,
            "recovered_at": incident[5].isoformat() if incident[5] else None,
        },
        {
            "id": str(evidence[0]),
            "bucket": evidence[1],
            "key": evidence[2],
            "filename": evidence[3],
            "sha256": evidence[4],
            "content_version": evidence[5],
            "byte_size": evidence[6],
        },
    )


def _read_object(client, *, bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def _artifact_exists_and_valid(client, *, bucket: str, key: str, schema: dict, statistics: dict) -> bool:
    if not object_exists(client, bucket=bucket, object_key=key):
        return False
    try:
        result = json.loads(_read_object(client, bucket=bucket, key=key).decode("utf-8"))
        validate_against_schema(result, schema, label="stored Hermes result")
        validate_log_triage_result(result, label="stored Hermes result")
        reconcile_result(result, statistics)
        validate_against_schema(result, schema, label="reconciled stored Hermes result")
        validate_log_triage_result(result, label="reconciled stored Hermes result")
        return True
    except Exception:
        return False


def _error_detail(exc: BaseException) -> tuple[str, str]:
    return str(getattr(exc, "error_code", "WORKER_FAILURE")), type(exc).__name__


def _process_log_triage_message(
    message: dict,
    *,
    settings,
    metrics: WorkerMetrics | None = None,
    hermes_client_factory=None,
) -> dict:
    """Process one claimed job; separated from RabbitMQ for contract tests."""
    if message.get("job_type") != "log_triage":
        raise WorkerInputError("log triage processor received an unsupported job type")
    if message.get("skill_name") != "log-triage-summary":
        raise SkillConfigurationError("unsupported analysis skill")

    db_conn = get_db_connection(settings.database_url)
    storage_client = get_client(settings)
    try:
        incident, evidence = _incident_and_evidence(db_conn, message)
        with tempfile.TemporaryDirectory(prefix=f"noc-job-{message['job_id']}-") as scratch:
            scratch_path = pathlib.Path(scratch)
            skill_root = scratch_path / "skills"
            materialize_snapshot(
                db_conn,
                message["skill_name"],
                message.get("skill_hash"),
                snapshot_id=message.get("skill_snapshot_id"),
                execution_hash=message.get("skill_execution_hash"),
                dest_root=skill_root,
            )
            skill_dir = skill_root / message["skill_name"]
            skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            schema = json.loads((skill_dir / "output.schema.json").read_text(encoding="utf-8"))

            log_path = scratch_path / "evidence.log"
            download_object(
                storage_client,
                bucket=evidence["bucket"],
                object_key=evidence["key"],
                dest_path=log_path,
                expected_sha256=evidence["sha256"],
                version_id=evidence["content_version"],
            )
            raw_bytes = log_path.read_bytes()
            try:
                log_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EvidenceVerificationError("log evidence is not valid UTF-8") from exc

            preprocessing_started = time.monotonic()
            statistics = preprocess_log(log_text)
            payload = build_hermes_input(
                job_id=message["job_id"],
                incident=incident,
                evidence=evidence,
                log_text=log_text,
                statistics=statistics,
            )
            preprocessing_duration_ms = round((time.monotonic() - preprocessing_started) * 1000)
            if metrics is not None:
                metrics.set_value("last_preprocessing_duration_ms", preprocessing_duration_ms)
            runtime = (hermes_client_factory or HermesClient)(settings)
            result = None
            output = None
            output_attempts = max(1, int(settings.hermes_max_output_attempts))
            for output_attempt in range(output_attempts):
                try:
                    result = runtime.analyze(
                        payload=payload,
                        skill_md=skill_md,
                        output_schema=schema,
                        repair_hint=(
                            "Group the annotated log entries by underlying operational cause. "
                            "Return evidence_entry_ids copied exactly from the [entry_id=N] "
                            "markers in log_excerpt; do not invent IDs or assign one entry to "
                            "more than one finding. Use pattern_ids:[\"unquantified\"] and "
                            "null count/percentage in the model response; the application "
                            "will calculate authoritative counts after reconciliation. "
                            "Return one concise bilingual result: summary_zh/summary_en "
                            "must be at most 800 characters and 2-4 sentences; key "
                            "details at most 600 characters and 3 sentences; secondary "
                            "details at most 320 characters and 1 sentence. Every "
                            "summary and detail must end with complete sentence "
                            "punctuation; never stop mid-sentence or use an ellipsis."
                            if output_attempt > 0
                            else None
                        ),
                    )
                    candidate = reconcile_result(result.result, statistics)
                    validate_against_schema(candidate, schema, label="Hermes output")
                    validate_log_triage_result(candidate, label="Hermes output")
                    output = candidate
                    break
                except HermesInvalidResponse:
                    if output_attempt + 1 >= output_attempts:
                        raise
                except (OutputValidationError, ValueError) as exc:
                    # A model can return JSON that is syntactically valid but
                    # violates the frozen schema/semantic contract (for
                    # example a wrong pattern ID or percentage). Give the
                    # runtime its bounded second chance before the existing
                    # terminal HERMES_INVALID_OUTPUT/DLQ path.
                    if output_attempt + 1 >= output_attempts:
                        # Preserve the deterministic boundary even when Hermes
                        # ignores the repair hint or returns a narrative that
                        # still violates the frozen limits. The fallback
                        # removes unsupported identities, compacts bounded
                        # narrative fields, restores authoritative counts, and
                        # must pass the same schema/semantic validators before
                        # it can be persisted. If it cannot, the original
                        # failure remains terminal.
                        try:
                            candidate = reconcile_result(
                                result.result,
                                statistics,
                                allow_unquantified_fallback=True,
                            )
                            compact_log_triage_narrative(candidate)
                            validate_against_schema(candidate, schema, label="Hermes fallback output")
                            validate_log_triage_result(candidate, label="Hermes fallback output")
                            output = candidate
                            break
                        except (KeyError, TypeError, ValueError, OutputValidationError) as fallback_error:
                            raise HermesInvalidResponse(
                                "Hermes returned invalid structured output"
                            ) from fallback_error
            assert result is not None and output is not None
            telemetry = {
                **result.telemetry,
                "hermes_version": settings.hermes_version,
                "runtime_version": settings.hermes_version,
                "input_hash": evidence["sha256"],
                "evidence_sha256": evidence["sha256"],
                "preprocessor_version": statistics["preprocessing_version"],
                "schema_version": payload["schema_version"],
                "raw_input_bytes": len(raw_bytes),
                "evidence_bytes": len(raw_bytes),
                "preprocessing_ratio": round(len(json.dumps(payload, ensure_ascii=False)) / max(1, len(raw_bytes)), 4),
                "confidence": output.get("confidence"),
                "output_attempts": output_attempt + 1,
                "preprocessing_duration_ms": preprocessing_duration_ms,
            }
            result_path = scratch_path / "result.json"
            telemetry_path = scratch_path / "telemetry.json"
            result_path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            telemetry_path.write_text(json.dumps(telemetry, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            upload_artifact(
                storage_client,
                bucket=settings.minio_bucket_job_artifacts,
                object_key=f"jobs/{message['job_id']}/result.json",
                src_path=result_path,
            )
            upload_artifact(
                storage_client,
                bucket=settings.minio_bucket_job_artifacts,
                object_key=f"jobs/{message['job_id']}/telemetry.json",
                src_path=telemetry_path,
            )
            return {"output": output, "telemetry": telemetry}
    finally:
        db_conn.close()


def _process_daily_report_message(
    message: dict,
    *,
    settings,
    metrics: WorkerMetrics | None = None,
    hermes_client_factory=None,
) -> dict:
    if message.get("job_type") != "daily_report":
        raise WorkerInputError("daily report processor received an unsupported job type")
    if message.get("skill_name") != "daily-alert-report":
        raise SkillConfigurationError("unsupported daily report skill")
    refs = message.get("object_refs") or []
    if message.get("incident_id") is not None or len(refs) != 1:
        raise WorkerInputError("daily report requires one frozen report snapshot")

    db_conn = get_db_connection(settings.database_url)
    storage_client = get_client(settings)
    try:
        with tempfile.TemporaryDirectory(prefix=f"noc-report-{message['job_id']}-") as scratch:
            scratch_path = pathlib.Path(scratch)
            skill_root = scratch_path / "skills"
            materialize_snapshot(
                db_conn,
                message["skill_name"],
                message.get("skill_hash"),
                snapshot_id=message.get("skill_snapshot_id"),
                execution_hash=message.get("skill_execution_hash"),
                dest_root=skill_root,
            )
            skill_dir = skill_root / message["skill_name"]
            skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            schema = json.loads((skill_dir / "output.schema.json").read_text(encoding="utf-8"))

            ref = refs[0]
            snapshot_path = scratch_path / "snapshot.json"
            download_object(
                storage_client,
                bucket=ref["bucket"],
                object_key=ref["key"],
                dest_path=snapshot_path,
                expected_sha256=ref.get("sha256"),
                version_id=ref.get("version_id"),
            )
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EvidenceIntegrityError("report snapshot is not valid JSON") from exc
            payload = build_daily_report_input(snapshot)
            report_bucket = settings.minio_bucket_reports
            keys = report_object_keys(message["job_id"])

            plan = load_saved_plan(
                storage_client,
                bucket=settings.minio_bucket_job_artifacts,
                job_id=message["job_id"],
            )
            telemetry = None
            if plan is not None:
                validate_against_schema(plan, schema, label="stored daily report plan")
                validate_daily_report_result(plan, label="stored daily report plan")
            else:
                runtime = (hermes_client_factory or HermesClient)(
                    settings,
                    profile=settings.hermes_profile,
                )
                output_attempts = max(1, int(settings.hermes_max_output_attempts))
                started = time.monotonic()
                for output_attempt in range(output_attempts):
                    try:
                        response = runtime.analyze(
                            payload=payload,
                            skill_md=skill_md,
                            output_schema=schema,
                            task="daily_report",
                        )
                        plan = response.result
                        validate_against_schema(plan, schema, label="Hermes daily report plan")
                        validate_daily_report_result(plan, label="Hermes daily report plan")
                        telemetry = {
                            **response.telemetry,
                            "hermes_version": settings.hermes_version,
                            "runtime_version": settings.hermes_version,
                            "runtime_profile": settings.hermes_profile,
                            "input_hash": ref.get("sha256"),
                            "snapshot_sha256": ref.get("sha256"),
                            "schema_version": payload["schema_version"],
                            "output_attempts": output_attempt + 1,
                            "duration_ms": round((time.monotonic() - started) * 1000),
                        }
                        break
                    except HermesInvalidResponse:
                        if output_attempt + 1 >= output_attempts:
                            raise
                    except (OutputValidationError, ValueError) as exc:
                        if output_attempt + 1 >= output_attempts:
                            raise HermesInvalidResponse("Hermes returned invalid daily report output") from exc
                if plan is None:
                    raise HermesInvalidResponse("Hermes returned no daily report plan")
                plan_path = scratch_path / "report-plan.json"
                plan_path.write_text(json.dumps(plan, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                upload_artifact(
                    storage_client,
                    bucket=settings.minio_bucket_job_artifacts,
                    object_key=keys["plan"],
                    src_path=plan_path,
                )
                if telemetry is not None:
                    telemetry_path = scratch_path / "report-telemetry.json"
                    telemetry_path.write_text(json.dumps(telemetry, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                    upload_artifact(
                        storage_client,
                        bucket=settings.minio_bucket_job_artifacts,
                        object_key=keys["telemetry"],
                        src_path=telemetry_path,
                    )

            existing = existing_report_artifacts(storage_client, bucket=report_bucket, job_id=message["job_id"])
            if existing is not None:
                return {
                    "output": plan,
                    "telemetry": telemetry or {},
                    "artifact_metadata": {
                        "report": existing["report"],
                        "document": existing["document"],
                        "screenshots": existing["screenshots"],
                        "provenance": {
                            "runtime_name": "hermes",
                            "runtime_version": settings.hermes_version,
                            "runtime_profile": settings.hermes_profile,
                            "provider": (telemetry or {}).get("provider"),
                            "runtime_model": (telemetry or {}).get("runtime_model"),
                            "input_manifest_sha256": ref.get("sha256"),
                            "output_sha256": hashlib.sha256(
                                json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                            ).hexdigest(),
                            "input_tokens": (telemetry or {}).get("input_tokens"),
                            "output_tokens": (telemetry or {}).get("output_tokens"),
                            "duration_ms": (telemetry or {}).get("duration_ms"),
                        },
                    },
                }

            docx_path = scratch_path / "report.docx"

            def screenshot_fetcher(bucket, object_key, version_id=None, expected_sha256=None):
                params = {"Bucket": bucket, "Key": object_key}
                if version_id:
                    params["VersionId"] = version_id
                body = storage_client.get_object(**params)["Body"].read()
                if expected_sha256 and hashlib.sha256(body).hexdigest() != expected_sha256:
                    raise EvidenceIntegrityError(f"screenshot checksum mismatch: {object_key}")
                return body

            preview, screenshots = compose_and_render(
                plan,
                snapshot,
                docx_path=docx_path,
                screenshot_fetcher=screenshot_fetcher,
            )
            preview_path = scratch_path / "document.json"
            preview_path.write_text(json.dumps(preview, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            screenshots_path = scratch_path / "document.screenshots.json"
            screenshots_path.write_text(json.dumps(screenshots, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

            report_metadata = upload_artifact_metadata(
                storage_client,
                bucket=report_bucket,
                object_key=keys["report"],
                src_path=docx_path,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            document_metadata = upload_artifact_metadata(
                storage_client,
                bucket=report_bucket,
                object_key=keys["document"],
                src_path=preview_path,
                content_type="application/json",
            )
            screenshots_metadata = upload_artifact_metadata(
                storage_client,
                bucket=report_bucket,
                object_key=keys["screenshots"],
                src_path=screenshots_path,
                content_type="application/json",
            )
            return {
                "output": plan,
                "telemetry": telemetry or {},
                "artifact_metadata": {
                    "report": report_metadata,
                    "document": document_metadata,
                    "screenshots": screenshots_metadata,
                    "provenance": {
                        "runtime_name": "hermes",
                        "runtime_version": settings.hermes_version,
                        "runtime_profile": settings.hermes_profile,
                        "provider": (telemetry or {}).get("provider"),
                        "runtime_model": (telemetry or {}).get("runtime_model"),
                        "input_manifest_sha256": ref.get("sha256"),
                        "output_sha256": hashlib.sha256(
                            json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        ).hexdigest(),
                        "input_tokens": (telemetry or {}).get("input_tokens"),
                        "output_tokens": (telemetry or {}).get("output_tokens"),
                        "duration_ms": (telemetry or {}).get("duration_ms"),
                    },
                },
            }
    finally:
        db_conn.close()


def process_message(
    message: dict,
    *,
    settings,
    metrics: WorkerMetrics | None = None,
    hermes_client_factory=None,
) -> dict:
    """Dispatch one provider-neutral job to its task-specific contract."""
    if message.get("job_type") == "log_triage":
        return _process_log_triage_message(
            message,
            settings=settings,
            metrics=metrics,
            hermes_client_factory=hermes_client_factory,
        )
    if message.get("job_type") == "daily_report":
        return _process_daily_report_message(
            message,
            settings=settings,
            metrics=metrics,
            hermes_client_factory=hermes_client_factory,
        )
    raise WorkerInputError(f"unsupported job type: {message.get('job_type')!r}")


class Worker:
    def __init__(self, settings, metrics: WorkerMetrics | None = None):
        self.settings = settings
        self.metrics = metrics or WorkerMetrics()
        self.storage_client = get_client(settings)

    def _settle_failure(self, channel, delivery_tag, message: dict, exc: BaseException) -> None:
        error_code, safe_message = _error_detail(exc)
        disposition = classify_failure(exc)
        job_id = uuid.UUID(message["job_id"])
        conn = get_db_connection(self.settings.database_url)
        try:
            claim_token = message.get("_claim_token")
            mark_failed(conn, job_id, error_code=error_code, error_message=safe_message, claim_token=claim_token)
            attempt = int(message.get("attempt", 1))
            if disposition != TERMINAL and attempt < self.settings.max_attempts:
                retry = {key: value for key, value in message.items() if not key.startswith("_")}
                retry["attempt"] = attempt + 1
                bump_attempt(conn, job_id, attempt + 1)
                names = queue_names(message["job_type"])
                channel.confirm_delivery()
                channel.basic_publish(
                    exchange=DLX_EXCHANGE,
                    routing_key=names["routing_key"],
                    body=json.dumps(retry).encode("utf-8"),
                    properties=None,
                )
                mark_retrying(conn, job_id, error_code=error_code, error_message=safe_message)
                self.metrics.increment("retry_count")
                LOG.warning(
                    "retry scheduled job_id=%s job_type=%s next_attempt=%s error_code=%s",
                    job_id,
                    message["job_type"],
                    attempt + 1,
                    error_code,
                )
            else:
                send_to_dlq(channel, job_type=message["job_type"], body={
                    "job_id": message["job_id"],
                    "job_type": message["job_type"],
                    "attempt": attempt,
                    "error_code": error_code,
                    "error": safe_message,
                })
                self.metrics.increment("jobs_failed")
                LOG.error(
                    "job moved to DLQ job_id=%s job_type=%s attempt=%s error_code=%s",
                    job_id,
                    message["job_type"],
                    attempt,
                    error_code,
                )
            channel.basic_ack(delivery_tag)
        finally:
            conn.close()

    def _update_queue_depth(self, channel) -> None:
        try:
            job_type = self.settings.worker_kind
            count = channel.queue_declare(queue_names(job_type)["main"], passive=True).method.message_count
            self.metrics.set_value("queue_depth", count)
        except Exception as exc:
            LOG.warning(
                "queue depth sample failed worker_kind=%s error_type=%s",
                self.settings.worker_kind,
                type(exc).__name__,
            )

    def handle_delivery(self, channel, method, _properties, body: bytes, expected_job_type: str = "log_triage") -> None:
        try:
            message = json.loads(body.decode("utf-8"))
            _validate_job_message(message)
        except Exception as exc:
            send_raw_to_dlq(channel, job_type=expected_job_type, body=body, reason=f"invalid job message: {type(exc).__name__}")
            channel.basic_ack(method.delivery_tag)
            return

        if message["job_type"] != expected_job_type:
            send_to_dlq(channel, job_type=expected_job_type, body=message)
            channel.basic_ack(method.delivery_tag)
            return

        job_id = uuid.UUID(message["job_id"])
        LOG.info(
            "job received job_id=%s job_type=%s worker_kind=%s attempt=%s",
            job_id,
            message["job_type"],
            self.settings.worker_kind,
            message.get("attempt", 1),
        )
        conn = get_db_connection(self.settings.database_url)
        try:
            claimed = claim_job(
                conn,
                job_id,
                worker_id=self.settings.worker_id,
                lease_seconds=self.settings.lease_seconds,
            )
            if claimed.disposition in {JobClaimDisposition.ALREADY_COMPLETED, JobClaimDisposition.NOT_CLAIMABLE}:
                channel.basic_ack(method.delivery_tag)
                return
            if claimed.disposition is JobClaimDisposition.LEASE_BUSY:
                channel.basic_nack(method.delivery_tag, requeue=False)
                return
            message["_claim_token"] = claimed["claim_token"]
            bump_attempt(conn, job_id, int(message.get("attempt", 1)))
            LOG.info(
                "job lease obtained job_id=%s job_type=%s worker_id=%s lease_seconds=%s",
                job_id,
                message["job_type"],
                self.settings.worker_id,
                self.settings.lease_seconds,
            )
        finally:
            conn.close()

        self.metrics.increment("jobs_processing")
        started = time.monotonic()
        lease_renewer = _LeaseRenewer(
            self.settings,
            job_id,
            message["_claim_token"],
        )
        lease_renewer.start()
        try:
            artifact_key = f"jobs/{message['job_id']}/result.json"
            # Crash reconciliation: an artifact written before a worker died
            # is the durable result for this immutable job and must not trigger
            # another provider call.
            if message["job_type"] == "log_triage":
                db_conn = get_db_connection(self.settings.database_url)
                try:
                    incident, evidence = _incident_and_evidence(db_conn, message)
                    with tempfile.TemporaryDirectory() as scratch:
                        skill_root = pathlib.Path(scratch) / "skills"
                        materialize_snapshot(
                            db_conn,
                            message["skill_name"],
                            message.get("skill_hash"),
                            snapshot_id=message.get("skill_snapshot_id"),
                            execution_hash=message.get("skill_execution_hash"),
                            dest_root=skill_root,
                        )
                        schema = json.loads((skill_root / message["skill_name"] / "output.schema.json").read_text())
                        evidence_path = pathlib.Path(scratch) / "reconcile-evidence.log"
                        download_object(
                            self.storage_client,
                            bucket=evidence["bucket"],
                            object_key=evidence["key"],
                            dest_path=evidence_path,
                            expected_sha256=evidence["sha256"],
                            version_id=evidence["content_version"],
                        )
                        statistics = preprocess_log(evidence_path.read_text(encoding="utf-8"))
                        if _artifact_exists_and_valid(self.storage_client, bucket=self.settings.minio_bucket_job_artifacts, key=artifact_key, schema=schema, statistics=statistics):
                            done_conn = get_db_connection(self.settings.database_url)
                            try:
                                if not mark_completed(done_conn, job_id, claim_token=message["_claim_token"]):
                                    raise JobLeaseError("job lease was lost during artifact reconciliation")
                            finally:
                                done_conn.close()
                            channel.basic_ack(method.delivery_tag)
                            self.metrics.increment("jobs_completed")
                            return
                finally:
                    db_conn.close()

            outcome = process_message(
                {key: value for key, value in message.items() if not key.startswith("_")},
                settings=self.settings,
                metrics=self.metrics,
            )
            done_conn = get_db_connection(self.settings.database_url)
            try:
                if not mark_completed(
                    done_conn,
                    job_id,
                    claim_token=message["_claim_token"],
                    artifact_metadata=outcome.get("artifact_metadata"),
                ):
                    raise JobLeaseError("job lease was lost before completion")
            finally:
                done_conn.close()
            channel.basic_ack(method.delivery_tag)
            self.metrics.increment("jobs_completed")
            LOG.info(
                "job completed job_id=%s job_type=%s duration_ms=%s",
                job_id,
                message["job_type"],
                round((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            if hasattr(exc, "error_code") and getattr(exc, "error_code") == "HERMES_INVALID_OUTPUT":
                self.metrics.increment("schema_validation_failures")
                LOG.error(
                    "result validation failed job_id=%s job_type=%s error_code=%s",
                    job_id,
                    message["job_type"],
                    getattr(exc, "error_code", "unknown"),
                )
            if (
                type(exc).__name__.startswith("Hermes")
                and getattr(exc, "error_code", None) != "HERMES_INVALID_OUTPUT"
            ):
                self.metrics.increment("provider_failures")
            self._settle_failure(channel, method.delivery_tag, message, exc)
        finally:
            lease_renewer.stop()
            self.metrics.increment("jobs_processing", -1)
            with self.metrics._lock:
                self.metrics.values["last_duration_ms"] = round((time.monotonic() - started) * 1000)

    def run_forever(self) -> None:
        while True:
            connection = None
            self.metrics.set_runtime_ready(False)
            self.metrics.set_value("rabbitmq_ready", False)
            self.metrics.set_value("hermes_profile_ready", False)
            try:
                connection = get_rabbit_connection(self.settings.rabbitmq_url)
                channel = connection.channel()
                declare_topology(channel)
                self.metrics.set_value("rabbitmq_ready", True)
                self._update_queue_depth(channel)
                profile = self.settings.hermes_profile
                LOG.info("Hermes profile verification started worker_kind=%s profile=%s", self.settings.worker_kind, profile)
                HermesClient(self.settings, profile=profile).verify_restricted_toolsets()
                LOG.info("Hermes profile verified worker_kind=%s profile=%s", self.settings.worker_kind, profile)
                self.metrics.set_value("hermes_profile_ready", True)
                channel.basic_qos(prefetch_count=1)
                job_type = self.settings.worker_kind
                channel.basic_consume(
                    queue_names(job_type)["main"],
                    lambda ch, method, properties, body: self.handle_delivery(
                        ch, method, properties, body, job_type
                    ),
                    auto_ack=False,
                )
                self.metrics.set_runtime_ready(True)
                LOG.info(
                    "AI Worker ready worker_kind=%s profile=%s concurrency=1",
                    job_type,
                    profile,
                )
                channel.start_consuming()
            except KeyboardInterrupt:
                return
            except Exception as exc:
                self.metrics.set_runtime_ready(False)
                # A failed profile check does not imply that RabbitMQ itself is
                # down. Preserve that distinction while the connection remains
                # usable; the next loop iteration starts all gates closed.
                self.metrics.set_value(
                    "rabbitmq_ready",
                    bool(connection is not None and not connection.is_closed),
                )
                self.metrics.set_value("hermes_profile_ready", False)
                LOG.error(
                    "worker degraded worker_kind=%s error_type=%s reconnect_seconds=5",
                    self.settings.worker_kind,
                    type(exc).__name__,
                )
                time.sleep(5)
            finally:
                self.metrics.set_runtime_ready(False)
                self.metrics.set_value("rabbitmq_ready", False)
                self.metrics.set_value("hermes_profile_ready", False)
                if connection is not None and not connection.is_closed:
                    connection.close()


def main() -> None:
    from noc_bridge.config import assert_runtime_secrets_are_safe, settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    assert_runtime_secrets_are_safe()
    metrics = WorkerMetrics()
    start_health_server(settings, metrics)
    Worker(settings, metrics).run_forever()


if __name__ == "__main__":
    main()
