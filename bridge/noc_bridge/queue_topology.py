"""RabbitMQ topology loaded from the repository's shared job protocol.

The runtime support package remains separately deployable and does not import
``apps/api``; both sides consume the shared contract instead of keeping
topology constants in sync by hand.
"""
from __future__ import annotations

import json
import pathlib
import uuid
import base64
from datetime import datetime, timezone

import pika
from pika.exchange_type import ExchangeType

_CONTRACTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "packages" / "contracts"
_job_protocol = json.loads((_CONTRACTS_DIR / "job_protocol.json").read_text())

PROTOCOL_VERSION = _job_protocol["protocol_version"]
JOB_TYPES = _job_protocol["job_types"]

JOBS_EXCHANGE = _job_protocol["exchanges"]["jobs"]
EVENTS_EXCHANGE = _job_protocol["exchanges"]["events"]
DLX_EXCHANGE = _job_protocol["exchanges"]["dead_letter"]

RETRY_TTL_MS = _job_protocol["retry_ttl_ms"]


def queue_names(job_type: str) -> dict:
    slug = job_type.replace("_", "-")
    return {
        "main": f"noc.jobs.{slug}",
        "retry": f"noc.jobs.{slug}.retry",
        "dlq": f"noc.jobs.{slug}.dlq",
        "routing_key": f"job.{job_type}",
    }


# The Phase 1 AI Worker consumes log-triage deliveries synchronously. Keep the
# heartbeat explicit so the protocol layer remains safe for long jobs.
CONNECTION_HEARTBEAT_SECONDS = 600


def get_connection(rabbitmq_url: str) -> pika.BlockingConnection:
    params = pika.URLParameters(rabbitmq_url)
    params.heartbeat = CONNECTION_HEARTBEAT_SECONDS
    return pika.BlockingConnection(params)


def declare_topology(channel) -> None:
    """Idempotent, identical to app.core.queue.declare_topology."""
    channel.exchange_declare(JOBS_EXCHANGE, ExchangeType.direct, durable=True)
    channel.exchange_declare(EVENTS_EXCHANGE, ExchangeType.topic, durable=True)
    channel.exchange_declare(DLX_EXCHANGE, ExchangeType.direct, durable=True)

    for job_type in JOB_TYPES:
        names = queue_names(job_type)

        channel.queue_declare(
            names["main"],
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX_EXCHANGE,
                "x-dead-letter-routing-key": names["routing_key"],
            },
        )
        channel.queue_bind(names["main"], JOBS_EXCHANGE, routing_key=names["routing_key"])

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

        channel.queue_declare(names["dlq"], durable=True)
        channel.queue_bind(names["dlq"], DLX_EXCHANGE, routing_key=f"{names['routing_key']}.dlq")

    channel.queue_declare("noc.events.log", durable=True)
    channel.queue_bind("noc.events.log", EVENTS_EXCHANGE, routing_key="event.job.#")


def publish_status_event(channel, *, job_id: str, event: str, detail: dict | None = None) -> None:
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
    names = queue_names(job_type)
    channel.confirm_delivery()
    channel.basic_publish(
        exchange=DLX_EXCHANGE,
        routing_key=f"{names['routing_key']}.dlq",
        body=json.dumps(body).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
    )


def send_raw_to_dlq(channel, *, job_type: str, body: bytes, reason: str) -> None:
    """Quarantine bytes that cannot be represented as a Job envelope.

    Invalid JSON cannot safely be wrapped as a fake Job because doing so
    would make operational tooling believe it was a valid protocol message.
    Keep a bounded, base64-encoded diagnostic payload instead; the original
    delivery is still acknowledged after this publish succeeds.
    """
    raw = bytes(body)
    max_bytes = 4096
    diagnostic = {
        "quarantine": "invalid_job_message",
        "reason": reason,
        "body_size": len(raw),
        "body_truncated": len(raw) > max_bytes,
        "body_base64": base64.b64encode(raw[:max_bytes]).decode("ascii"),
    }
    send_to_dlq(channel, job_type=job_type, body=diagnostic)
