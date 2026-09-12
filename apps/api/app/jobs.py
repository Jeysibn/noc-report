"""
Job publisher service — ties a `Job` DB record (source of truth, per §26:
"RabbitMQ is not the source of truth") to a RabbitMQ message. Milestones
13/14 (real log triage, real daily report) will call `enqueue_job()` to
kick off Claude Bridge work; nothing calls it yet since this milestone
doesn't build a producer-side feature, only the queue infrastructure
itself.
"""

import uuid
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.queue import declare_topology, get_connection, publish_job
from app.models.models import Job


@contextmanager
def open_channel():
    connection = get_connection()
    try:
        channel = connection.channel()
        declare_topology(channel)
        yield channel
    finally:
        connection.close()


def enqueue_job(
    db: Session,
    channel,
    *,
    job_type: str,
    requested_by: uuid.UUID | None,
    incident_id: str | None,
    object_refs: list[dict],
    model: str,
    effort: str,
    skill_name: str,
    skill_version: str,
) -> Job:
    job = Job(
        job_type=job_type,
        status="QUEUED",
        incident_id=uuid.UUID(incident_id) if incident_id else None,
        requested_by=requested_by,
        model=model,
        effort=effort,
        skill_name=skill_name,
        skill_version=skill_version,
        attempt=1,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(job)
    db.flush()  # assigns job.id without committing yet

    publish_job(
        channel,
        job_id=job.id,
        job_type=job_type,
        incident_id=incident_id,
        object_refs=object_refs,
        model=model,
        effort=effort,
        skill_name=skill_name,
        skill_version=skill_version,
        correlation_id=job.correlation_id,
    )
    db.commit()
    db.refresh(job)
    return job
