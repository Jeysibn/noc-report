"""
RabbitMQ topology and job publisher — master plan §26.

Milestone 11 scope: exchanges, durable queues, retry queues, DLQs, a job
publisher, job records, status events. The consumer side (host
`noc-claude-bridge.service`, Docker sandboxing, actually running Claude
Code CLI) is Milestone 12 and deliberately not built here (coding-agent
rule 20) — this module only declares topology and publishes/ACKs
messages; nothing in this milestone consumes a job queue to do real work.

Topology exactly as specified:

    exchanges: noc.jobs, noc.events, noc.dlx
    queues:    noc.jobs.{log-triage,daily-report}[.retry|.dlq]
    routing:   job.log_triage, job.daily_report,
               event.job.{started,progress,completed,failed}

ADR-004: messages carry references only (job ID, incident/report IDs,
MinIO object refs, model/effort/skill, correlation metadata) — never
file bytes.
"""

import json
import pathlib
import uuid
from datetime import datetime, timezone

import jsonschema
import pika
from pika.exchange_type import ExchangeType

from app.core.config import settings

# Skill Runtime mission Phase 14: the job message protocol's single source
# of truth is this schema file, shared with the bridge (which validates the
# same file against its own incoming payloads in
# bridge/noc_bridge/service.py) rather than each side's shape living only
# in Python code kept in sync by hand.
_CONTRACTS_DIR = pathlib.Path(__file__).resolve().parents[4] / "packages" / "contracts"
_JOB_MESSAGE_SCHEMA_PATH = _CONTRACTS_DIR / "job_message.schema.json"
_job_message_schema = json.loads(_JOB_MESSAGE_SCHEMA_PATH.read_text())
_job_protocol = json.loads((_CONTRACTS_DIR / "job_protocol.json").read_text())

PROTOCOL_VERSION = _job_protocol["protocol_version"]
JOB_TYPES = _job_protocol["job_types"]

JOBS_EXCHANGE = _job_protocol["exchanges"]["jobs"]
EVENTS_EXCHANGE = _job_protocol["exchanges"]["events"]
DLX_EXCHANGE = _job_protocol["exchanges"]["dead_letter"]

# Retry queues route back to the DLX after their own TTL expires, so a
# message that has failed once waits before being redelivered to the main
# queue rather than hammering a struggling consumer immediately.
RETRY_TTL_MS = _job_protocol["retry_ttl_ms"]


def _queue_names(job_type: str) -> dict:
    slug = job_type.replace("_", "-")
    return {
        "main": f"noc.jobs.{slug}",
        "retry": f"noc.jobs.{slug}.retry",
        "dlq": f"noc.jobs.{slug}.dlq",
        "routing_key": f"job.{job_type}",
    }


# Public alias — Reliability mission Batch A: app/jobs.py and app/outbox.py
# both need queue name lookups (routing key at OutboxEvent-creation time,
# main-queue name at dispatch time) without reaching into a "private"
# helper.
queue_names = _queue_names


def build_job_message(
    *,
    job_id: uuid.UUID,
    job_type: str,
    incident_id: str | None,
    object_refs: list[dict],
    model: str | None,
    effort: str | None,
    skill_name: str,
    skill_version: str,
    correlation_id: str,
    attempt: int = 1,
    skill_hash: str | None = None,
    skill_execution_hash: str | None = None,
    skill_snapshot_id: uuid.UUID | None = None,
    protocol_version: int = PROTOCOL_VERSION,
    expected_output_type: str | None = None,
    ai_policy: dict | None = None,
) -> dict:
    """The exact message body `publish_job` used to build inline. Split
    out (Reliability mission Batch A) so app/jobs.py can persist it into
    an OutboxEvent.payload at Job-creation time — the dispatcher later
    publishes this same dict verbatim, so the message on the wire is
    identical to what it always was.

    `skill_hash` (Reliability mission Batch B — Skill Registry) is the
    content hash of the SkillSnapshot the API resolved at enqueue time;
    the bridge recomputes its own local hash for the same skill_name
    before executing the job and refuses to run on a mismatch (drift
    protection — see bridge/noc_bridge/skill_registry.py). Optional/None
    for callers that haven't been updated to pass it (tests, tooling)."""
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job_type: {job_type}")
    message = {
        "protocol_version": protocol_version,
        "job_id": str(job_id),
        "job_type": job_type,
        "incident_id": incident_id,
        "object_refs": object_refs,
        "model": model,
        "effort": effort,
        "skill_name": skill_name,
        "skill_version": skill_version,
        "skill_hash": skill_hash,
        "skill_execution_hash": skill_execution_hash,
        "skill_snapshot_id": str(skill_snapshot_id) if skill_snapshot_id else None,
        "expected_output_type": expected_output_type or f"{job_type}.result",
        "ai_policy": ai_policy if ai_policy is not None else {
            key: value for key, value in {"model": model, "effort": effort}.items()
            if value is not None
        },
        "correlation_id": correlation_id,
        "attempt": attempt,
    }
    # Skill Runtime mission Phase 14: validate against the canonical
    # protocol schema before it's ever persisted into an OutboxEvent.payload
    # or published — a producer-side bug that drifts from the protocol is
    # caught here, at message-build time, rather than surfacing later as a
    # bridge-side KeyError deep inside job processing.
    jsonschema.validate(
        message,
        _job_message_schema,
        format_checker=jsonschema.FormatChecker(),
    )
    return message


def get_connection() -> pika.BlockingConnection:
    return pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))


def declare_topology(channel) -> None:
    """Idempotent — mirrors app/seed.py and app/core/storage.ensure_buckets()."""
    channel.exchange_declare(JOBS_EXCHANGE, ExchangeType.direct, durable=True)
    channel.exchange_declare(EVENTS_EXCHANGE, ExchangeType.topic, durable=True)
    channel.exchange_declare(DLX_EXCHANGE, ExchangeType.direct, durable=True)

    for job_type in JOB_TYPES:
        names = _queue_names(job_type)

        # Main queue: failed messages (nacked/rejected) route to the DLX
        # under the same routing key, which the retry queue is bound to.
        channel.queue_declare(
            names["main"],
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX_EXCHANGE,
                "x-dead-letter-routing-key": names["routing_key"],
            },
        )
        channel.queue_bind(names["main"], JOBS_EXCHANGE, routing_key=names["routing_key"])

        # Retry queue: no consumer ever reads this directly — messages sit
        # here for RETRY_TTL_MS then dead-letter *back* onto the main
        # queue's exchange/routing key for redelivery (§26 "bounded retries").
        channel.queue_declare(
            names["retry"],
            durable=True,
            arguments={
                "x-dead-letter-exchange": JOBS_EXCHANGE,
                "x-dead-letter-routing-key": names["routing_key"],
                "x-message-ttl": RETRY_TTL_MS,
            },
        )
        channel.queue_bind(names["retry"], DLX_EXCHANGE, routing_key=names["routing_key"])

        # DLQ: terminal — where messages land after retry exhaustion (the
        # publisher/consumer decides exhaustion by attempt count, then
        # publishes directly here rather than back through the DLX).
        channel.queue_declare(names["dlq"], durable=True)
        channel.queue_bind(names["dlq"], DLX_EXCHANGE, routing_key=f"{names['routing_key']}.dlq")

    channel.queue_declare("noc.events.log", durable=True)
    channel.queue_bind("noc.events.log", EVENTS_EXCHANGE, routing_key="event.job.#")


def publish_job(
    channel,
    *,
    job_id: uuid.UUID,
    job_type: str,
    incident_id: str | None,
    object_refs: list[dict],
    model: str | None,
    effort: str | None,
    skill_name: str,
    skill_version: str,
    correlation_id: str,
    attempt: int = 1,
    skill_hash: str | None = None,
    skill_execution_hash: str | None = None,
    skill_snapshot_id: uuid.UUID | None = None,
) -> None:
    """Publish a job message directly. ADR-004: references only, never
    file bytes. Kept for tests/tools that want a one-shot publish without
    going through the outbox; the real dispatch path (app/outbox.py) uses
    publish_message with the routing_key/payload already persisted on the
    OutboxEvent row."""
    names = _queue_names(job_type)
    body = build_job_message(
        job_id=job_id,
        job_type=job_type,
        incident_id=incident_id,
        object_refs=object_refs,
        model=model,
        effort=effort,
        skill_name=skill_name,
        skill_version=skill_version,
        correlation_id=correlation_id,
        attempt=attempt,
        skill_hash=skill_hash,
        skill_execution_hash=skill_execution_hash,
        skill_snapshot_id=skill_snapshot_id,
    )
    publish_message(
        channel,
        exchange=JOBS_EXCHANGE,
        routing_key=names["routing_key"],
        payload=body,
        message_id=str(job_id),
    )


def publish_message(
    channel,
    *,
    exchange: str,
    routing_key: str,
    payload: dict,
    message_id: str | None = None,
) -> None:
    """Reliability mission Batch A: the one place that actually calls
    `channel.basic_publish` with publisher confirms turned on, used both
    by `publish_job` above and by the outbox dispatcher (which republishes
    an OutboxEvent.payload verbatim). `channel.confirm_delivery()` makes
    `basic_publish` raise on a nack/return from the broker, so the
    dispatcher can tell a real publish failure apart from success and
    leave the OutboxEvent unpublished (retryable) rather than mark it
    published on a publish the broker never actually confirmed."""
    channel.confirm_delivery()
    channel.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=json.dumps(payload).encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent,
            content_type="application/json",
            message_id=message_id,
        ),
        mandatory=True,
    )


def publish_status_event(channel, *, job_id: uuid.UUID, event: str, detail: dict | None = None) -> None:
    """event: started | progress | completed | failed (§26 routing keys)."""
    body = {
        "job_id": str(job_id),
        "event": event,
        "detail": detail or {},
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }
    channel.confirm_delivery()
    channel.basic_publish(
        exchange=EVENTS_EXCHANGE,
        routing_key=f"event.job.{event}",
        body=json.dumps(body).encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent,
            content_type="application/json",
        ),
    )


def send_to_dlq(channel, *, job_type: str, body: dict) -> None:
    """Used once a job has exhausted its retry attempts (§26 "DLQ after
    retry exhaustion") — published directly rather than relying on the
    retry queue's own TTL/DLX chain, since attempt-counting is the
    consumer's job, not the broker's."""
    names = _queue_names(job_type)
    channel.confirm_delivery()
    channel.basic_publish(
        exchange=DLX_EXCHANGE,
        routing_key=f"{names['routing_key']}.dlq",
        body=json.dumps(body).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
    )


def queue_message_count(channel, queue_name: str) -> int:
    result = channel.queue_declare(queue_name, durable=True, passive=True)
    return result.method.message_count


def requeue_one_dlq_message(channel, *, job_type: str) -> dict | None:
    """Milestone 17 (DLQ controls). Pops the oldest message off
    job_type's DLQ and republishes it onto the main queue with attempt
    reset to 1, for one more real try. Returns the decoded payload (so
    the caller can reconcile the Job row's status), or None if the DLQ
    was empty."""
    names = _queue_names(job_type)
    method, properties, body = channel.basic_get(names["dlq"], auto_ack=False)
    if method is None:
        return None

    payload = json.loads(body)
    payload["attempt"] = 1
    channel.confirm_delivery()
    channel.basic_publish(
        exchange=JOBS_EXCHANGE,
        routing_key=names["routing_key"],
        body=json.dumps(payload).encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent,
            content_type="application/json",
            message_id=properties.message_id,
        ),
        mandatory=True,
    )
    channel.basic_ack(method.delivery_tag)
    return payload


def purge_dlq(channel, *, job_type: str) -> int:
    """Milestone 17 (DLQ controls). Permanently discards every message
    on job_type's DLQ — the corresponding Job rows stay FAILED (this
    does not touch Postgres), since §26 treats Postgres, not RabbitMQ,
    as the source of truth for job state."""
    names = _queue_names(job_type)
    result = channel.queue_purge(names["dlq"])
    return result.method.message_count
