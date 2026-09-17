"""Shared predicates for report metrics.

The Dashboard and Analytics are different views, but they must agree on what
"generated" means.  A Report request is generated only after its Job is
completed and the immutable DOCX artifact has been pinned.
"""
from sqlalchemy import and_, func

from app.models.models import Job, Report


def generated_report_predicate():
    """Return the authoritative SQL predicate for a generated Report."""
    return and_(
        Job.status == "COMPLETED",
        Report.report_version_id.is_not(None),
    )


def generated_report_time():
    """Return the durable completion time used by time-window metrics.

    Job.completed_at is written by the bridge in the same completion path as
    the artifact identity.  Report.generated_at is retained as a compatibility
    fallback for rows completed before that field was populated by polling.
    """
    return func.coalesce(Job.completed_at, Report.generated_at)
