"""Config-drift guard (Reliability mission Batch C, Phase 15).

bridge/noc_bridge/queue_topology.py deliberately duplicates
apps/api/app/core/queue.py's RabbitMQ topology rather than importing it —
the bridge is a separate host-side deployable that shouldn't need apps/api
on its PYTHONPATH (see queue_topology.py's own module docstring). That
convention only stays safe if the two files' topology constants are
actually kept in sync by hand, as promised — this test fails CI the moment
they diverge, rather than relying on someone noticing in review.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("noc_bridge.queue_topology")

from noc_bridge import queue_topology  # noqa: E402

_API_QUEUE_PY = pathlib.Path(__file__).resolve().parents[2] / "apps" / "api" / "app" / "core" / "queue.py"


def _api_queue_source() -> str:
    return _API_QUEUE_PY.read_text()


def test_job_types_match():
    source = _api_queue_source()
    assert 'JOB_TYPES = ["log_triage", "daily_report"]' in source
    assert queue_topology.JOB_TYPES == ["log_triage", "daily_report"]


def test_exchange_names_match():
    source = _api_queue_source()
    for name, value in (
        ("JOBS_EXCHANGE", queue_topology.JOBS_EXCHANGE),
        ("EVENTS_EXCHANGE", queue_topology.EVENTS_EXCHANGE),
        ("DLX_EXCHANGE", queue_topology.DLX_EXCHANGE),
    ):
        assert f'{name} = "{value}"' in source


def test_retry_ttl_matches():
    import re

    source = _api_queue_source()
    match = re.search(r"RETRY_TTL_MS = ([0-9_]+)", source)
    assert match is not None, "apps/api/app/core/queue.py no longer defines RETRY_TTL_MS"
    assert int(match.group(1).replace("_", "")) == queue_topology.RETRY_TTL_MS
