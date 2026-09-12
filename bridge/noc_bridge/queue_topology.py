"""RabbitMQ topology — kept in sync BY HAND with
`apps/api/app/core/queue.py` (master plan §26). Duplicated rather than
imported because the bridge is a separate host-side deployable
(master plan §27) that shouldn't require apps/api on its PYTHONPATH.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pika
from pika.exchange_type import ExchangeType

JOB_TYPES = ["log_triage", "daily_report"]

JOBS_EXCHANGE = "noc.jobs"
EVENTS_EXCHANGE = "noc.events"
DLX_EXCHANGE = "noc.dlx"

RETRY_TTL_MS = 30_000


def queue_names(job_type: str) -> dict:
    slug = job_type.replace("_", "-")
    return {
        "main": f"noc.jobs.{slug}",
        "retry": f"noc.jobs.{slug}.retry",
        "dlq": f"noc.jobs.{slug}.dlq",
        "routing_key": f"job.{job_type}",
    }


# _handle_delivery runs a job's whole sandbox lifecycle (docker container
# create/wait, up to the sandbox's own timeout) synchronously inside pika's
# on_message_callback, so the BlockingConnection's IO loop can't service
# heartbeats for that entire span. With RabbitMQ's default ~60s heartbeat,
# any job that runs longer than a couple of missed intervals gets its
# connection killed by the broker mid-job (seen in practice: a
# StreamLostError killed the whole process, uncaught, taking the bridge
# down with a job stuck at PROCESSING forever). A heartbeat comfortably
# longer than the sandbox's own job timeout makes that starvation far less
# likely; run_forever()'s reconnect loop is the backstop for when it still
# happens.
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
