"""RabbitMQ AI Worker for Phase 1 Hermes log analysis.

This process owns the application-side job lifecycle and storage access. It
does not implement an agent loop or provider SDK; Hermes does that behind its
authenticated HTTP boundary.
"""
from __future__ import annotations

import json
import logging
import pathlib
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jsonschema

from noc_bridge.db import (
    JobClaimDisposition,
    bump_attempt,
    claim_job,
    fetch_job_row,
    get_connection as get_db_connection,
    mark_completed,
    mark_failed,
    renew_job_lease,
)
from noc_bridge.failures import TERMINAL, classify_failure
from noc_bridge.hermes import HermesClient, HermesInvalidResponse
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
)
from noc_bridge.validation import validate_against_schema, validate_log_triage_result
from preprocessing import build_hermes_input, preprocess_log, reconcile_result


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


class WorkerMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.values = {
            "jobs_queued": 0,
            "jobs_processing": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "retry_count": 0,
            "schema_validation_failures": 0,
            "provider_failures": 0,
            "last_duration_ms": 0,
        }

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self.values[name] = self.values.get(name, 0) + value

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.values)


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
            except Exception:
                LOG.exception("job lease renewal failed job_id=%s", self.job_id)
            finally:
                if conn is not None:
                    conn.close()


class _HealthHandler(BaseHTTPRequestHandler):
    metrics: WorkerMetrics | None = None
    runtime_profile: str = "noc-log-analysis"

    def do_GET(self):  # noqa: N802 - stdlib HTTP handler API
        if self.path not in {"/health", "/metrics"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "status": "healthy",
            "component": "ai-worker",
            "runtime": "hermes",
            "profile": self.runtime_profile,
            "metrics": self.metrics.snapshot() if self.metrics else {},
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def start_health_server(settings, metrics: WorkerMetrics):
    handler = type("WorkerHealthHandler", (_HealthHandler,), {
        "metrics": metrics,
        "runtime_profile": settings.hermes_profile,
    })
    server = ThreadingHTTPServer((settings.health_host, settings.health_port), handler)
    thread = threading.Thread(target=server.serve_forever, name="ai-worker-health", daemon=True)
    thread.start()
    return server


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


def process_message(message: dict, *, settings, metrics: WorkerMetrics | None = None) -> dict:
    """Process one claimed job; separated from RabbitMQ for contract tests."""
    if message.get("job_type") != "log_triage":
        raise WorkerInputError("Phase 1 worker only accepts log_triage jobs")
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

            payload = build_hermes_input(
                job_id=message["job_id"],
                incident=incident,
                evidence=evidence,
                log_text=log_text,
            )
            statistics = payload["statistics"]
            runtime = HermesClient(settings)
            result = None
            output_attempts = max(1, int(settings.hermes_max_output_attempts))
            for output_attempt in range(output_attempts):
                try:
                    result = runtime.analyze(
                        payload=payload,
                        skill_md=skill_md,
                        output_schema=schema,
                    )
                    break
                except HermesInvalidResponse:
                    if output_attempt + 1 >= output_attempts:
                        raise
            assert result is not None
            output = reconcile_result(result.result, statistics)
            validate_against_schema(output, schema, label="Hermes output")
            validate_log_triage_result(output, label="Hermes output")
            telemetry = {
                **result.telemetry,
                "hermes_version": settings.hermes_version,
                "input_hash": evidence["sha256"],
                "evidence_sha256": evidence["sha256"],
                "preprocessor_version": statistics["preprocessing_version"],
                "schema_version": payload["schema_version"],
                "raw_input_bytes": len(raw_bytes),
                "evidence_bytes": len(raw_bytes),
                "preprocessing_ratio": round(len(json.dumps(payload, ensure_ascii=False)) / max(1, len(raw_bytes)), 4),
                "confidence": output.get("confidence"),
                "output_attempts": output_attempt + 1,
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


class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.metrics = WorkerMetrics()
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
                names = queue_names(message["job_type"])
                channel.confirm_delivery()
                channel.basic_publish(
                    exchange=DLX_EXCHANGE,
                    routing_key=names["routing_key"],
                    body=json.dumps(retry).encode("utf-8"),
                    properties=None,
                )
                self.metrics.increment("retry_count")
            else:
                send_to_dlq(channel, job_type=message["job_type"], body={
                    "job_id": message["job_id"],
                    "job_type": message["job_type"],
                    "attempt": attempt,
                    "error_code": error_code,
                    "error": safe_message,
                })
                self.metrics.increment("jobs_failed")
            channel.basic_ack(delivery_tag)
        finally:
            conn.close()

    def handle_delivery(self, channel, method, _properties, body: bytes) -> None:
        try:
            message = json.loads(body.decode("utf-8"))
            _validate_job_message(message)
        except Exception as exc:
            send_raw_to_dlq(channel, job_type="log_triage", body=body, reason=f"invalid job message: {type(exc).__name__}")
            channel.basic_ack(method.delivery_tag)
            return

        if message["job_type"] != "log_triage":
            send_to_dlq(channel, job_type="log_triage", body=message)
            channel.basic_ack(method.delivery_tag)
            return

        job_id = uuid.UUID(message["job_id"])
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

            process_message({key: value for key, value in message.items() if not key.startswith("_")}, settings=self.settings, metrics=self.metrics)
            done_conn = get_db_connection(self.settings.database_url)
            try:
                if not mark_completed(done_conn, job_id, claim_token=message["_claim_token"]):
                    raise JobLeaseError("job lease was lost before completion")
            finally:
                done_conn.close()
            channel.basic_ack(method.delivery_tag)
            self.metrics.increment("jobs_completed")
        except Exception as exc:
            if hasattr(exc, "error_code") and getattr(exc, "error_code") == "HERMES_INVALID_OUTPUT":
                self.metrics.increment("schema_validation_failures")
            if type(exc).__name__.startswith("Hermes"):
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
            try:
                connection = get_rabbit_connection(self.settings.rabbitmq_url)
                channel = connection.channel()
                declare_topology(channel)
                channel.basic_qos(prefetch_count=1)
                channel.basic_consume(queue_names("log_triage")["main"], self.handle_delivery, auto_ack=False)
                LOG.info("AI Worker ready profile=%s concurrency=%s", self.settings.hermes_profile, self.settings.max_concurrency)
                channel.start_consuming()
            except KeyboardInterrupt:
                return
            except Exception:
                LOG.exception("AI Worker broker loop failed; reconnecting")
                time.sleep(5)
            finally:
                if connection is not None and not connection.is_closed:
                    connection.close()


def main() -> None:
    from noc_bridge.config import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    metrics = WorkerMetrics()
    start_health_server(settings, metrics)
    Worker(settings).run_forever()


if __name__ == "__main__":
    main()
