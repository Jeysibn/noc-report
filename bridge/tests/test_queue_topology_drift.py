"""Shared job-protocol contract guard.

The API and bridge are separately deployable, so they load the same JSON
protocol rather than importing one another or duplicating topology literals.
"""
from __future__ import annotations

import json
import pathlib

import pytest

pytest.importorskip("noc_bridge.queue_topology")

from noc_bridge import queue_topology  # noqa: E402

_API_QUEUE_PY = pathlib.Path(__file__).resolve().parents[2] / "apps" / "api" / "app" / "core" / "queue.py"


_PROTOCOL = json.loads((_API_QUEUE_PY.parents[4] / "packages" / "contracts" / "job_protocol.json").read_text())


def test_job_types_match():
    assert queue_topology.JOB_TYPES == _PROTOCOL["job_types"]


def test_exchange_names_match():
    assert queue_topology.JOBS_EXCHANGE == _PROTOCOL["exchanges"]["jobs"]
    assert queue_topology.EVENTS_EXCHANGE == _PROTOCOL["exchanges"]["events"]
    assert queue_topology.DLX_EXCHANGE == _PROTOCOL["exchanges"]["dead_letter"]


def test_retry_ttl_matches():
    assert queue_topology.RETRY_TTL_MS == _PROTOCOL["retry_ttl_ms"]
