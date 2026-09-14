"""Operational calendar projection for frozen shift reports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class ShiftTimeProjection:
    """Localized shift times derived only from immutable snapshot values."""

    timezone_name: str
    local_start: datetime
    local_end: datetime | None

    @property
    def report_date(self) -> str:
        return self.local_start.strftime("%A, %d %B %Y")


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    # Snapshot timestamps are UTC by contract. Treat a legacy naive value as
    # UTC so old reports remain deterministic rather than using host timezone.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def project_shift_time(snapshot: dict) -> ShiftTimeProjection:
    """Project a frozen report snapshot into its operational calendar."""
    timezone_name = str(snapshot.get("shift_timezone") or "UTC").strip()
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid frozen shift timezone: {timezone_name!r}") from exc
    starts_at = snapshot.get("shift_starts_at")
    if not starts_at:
        raise ValueError("frozen report snapshot is missing shift_starts_at")
    ends_at = snapshot.get("shift_ends_at")
    return ShiftTimeProjection(
        timezone_name=timezone_name,
        local_start=_parse_timestamp(starts_at).astimezone(zone),
        local_end=_parse_timestamp(ends_at).astimezone(zone) if ends_at else None,
    )
