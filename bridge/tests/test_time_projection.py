from datetime import datetime

import pytest

from noc_bridge.time_projection import project_shift_time


def test_manila_projection_uses_operational_calendar_date_at_utc_boundary():
    projection = project_shift_time({
        "shift_starts_at": "2026-09-14T17:00:00Z",
        "shift_ends_at": "2026-09-15T01:00:00Z",
        "shift_timezone": "Asia/Manila",
    })
    assert projection.local_start == datetime.fromisoformat("2026-09-15T01:00:00+08:00")
    assert projection.report_date == "Tuesday, 15 September 2026"


def test_utc_projection_does_not_shift_calendar_date():
    projection = project_shift_time({
        "shift_starts_at": "2026-09-14T17:00:00Z",
        "shift_timezone": "UTC",
    })
    assert projection.report_date == "Monday, 14 September 2026"


def test_dst_projection_uses_zone_rules_not_a_fixed_offset():
    before = project_shift_time({
        "shift_starts_at": "2026-03-08T06:30:00Z",
        "shift_timezone": "America/New_York",
    })
    after = project_shift_time({
        "shift_starts_at": "2026-03-08T07:30:00Z",
        "shift_timezone": "America/New_York",
    })
    assert before.local_start.isoformat() == "2026-03-08T01:30:00-05:00"
    assert after.local_start.isoformat() == "2026-03-08T03:30:00-04:00"


def test_invalid_or_missing_start_is_explicit():
    with pytest.raises(ValueError, match="missing shift_starts_at"):
        project_shift_time({"shift_timezone": "UTC"})
    with pytest.raises(ValueError, match="invalid frozen shift timezone"):
        project_shift_time({"shift_starts_at": "2026-01-01T00:00:00Z", "shift_timezone": "Mars/Colony"})
