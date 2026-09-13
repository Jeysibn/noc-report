"""Job creation — Reliability mission Batch A (transactional outbox).

`enqueue_job()` used to publish straight to RabbitMQ between a flush and a
commit (§26 "RabbitMQ is not the source of truth" was honored for reads,
but not for writes: the publish itself happened before the Job — and, in
the caller, before the AnalysisRun/Report/ReportSnapshot the message
implicitly depends on — was durable). That left a real crash window: if
the process died after the Job commit but before the caller's own commit
of the AnalysisRun/Report, the bridge could receive and complete a job
for domain state that didn't exist yet.

`enqueue_job()` now only ever writes to Postgres: it creates the Job row
*and* an OutboxEvent row describing the RabbitMQ message that must
eventually be published, in the same session as everything else the
caller is about to commit. Nothing is published here. A separate outbox
dispatcher (app/outbox.py) publishes OutboxEvent rows strictly after they
have committed, so a RabbitMQ message can never outrun the domain state
it depends on.
"""

import uuid
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.queue import build_job_message, declare_topology, get_connection, queue_names
from app.models.models import Job, OutboxEvent


@contextmanager
def open_channel():
    """Still used by the outbox dispatcher (app/outbox.py) and by admin
    DLQ controls — request handlers no longer need a RabbitMQ channel at
    all, since enqueue_job only writes to Postgres now."""
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        yield channel
    finally:
        connection.close()


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    requested_by: uuid.UUID | None,
    incident_id: str | None,
    object_refs: list[dict],
    model: str,
    effort: str,
    skill_name: str,
    skill_version: str,
    skill_hash: str | None = None,
    skill_snapshot_id: uuid.UUID | None = None,
) -> Job:
    """`skill_hash` (Reliability mission Batch B — Skill Registry): pass
    the content hash of the SkillSnapshot resolved via
    `app/skills/registry.py::get_or_create_snapshot` at the call site.
    Optional so existing callers/tests that haven't been updated still
    work; a job enqueued without one just skips the bridge's drift check
    (bridge/noc_bridge/skill_registry.py treats a missing hash as
    "nothing to verify," not as a mismatch)."""
    job = Job(
        job_type=job_type,
        status="QUEUED",
        incident_id=uuid.UUID(incident_id) if incident_id else None,
        requested_by=requested_by,
        model=model,
        effort=effort,
        skill_name=skill_name,
        skill_version=skill_version,
        skill_hash=skill_hash,
        skill_snapshot_id=skill_snapshot_id,
        attempt=1,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(job)
    db.flush()  # assigns job.id — no commit; caller commits once, atomically

    message = build_job_message(
        job_id=job.id,
        job_type=job_type,
        incident_id=incident_id,
        object_refs=object_refs,
        model=model,
        effort=effort,
        skill_name=skill_name,
        skill_version=skill_version,
        skill_hash=skill_hash,
        skill_snapshot_id=str(skill_snapshot_id) if skill_snapshot_id else None,
        correlation_id=job.correlation_id,
    )
    names = queue_names(job_type)
    db.add(
        OutboxEvent(
            event_type="job.dispatch",
            aggregate_type="job",
            aggregate_id=job.id,
            job_id=job.id,
            routing_key=names["routing_key"],
            payload=message,
        )
    )
    db.flush()
    return job
