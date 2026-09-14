"""Canonical incident-set queries shared by readiness and report freezing."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.models import Incident


def shift_incident_statement(shift_id: uuid.UUID):
    """Return the authoritative report/readiness incident scope for a shift."""
    return select(Incident).where(Incident.shift_id == shift_id).order_by(Incident.created_at)
