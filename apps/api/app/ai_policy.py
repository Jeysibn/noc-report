"""Authoritative API-side validation for generic runtime infrastructure."""
from __future__ import annotations

import json
from pathlib import Path

_POLICY_PATH = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "ai_execution_policy.json"
POLICY = json.loads(_POLICY_PATH.read_text())
TIMEOUT_MIN = POLICY["job_timeout_seconds"]["min"]
TIMEOUT_MAX = POLICY["job_timeout_seconds"]["max"]
CONCURRENCY_MIN = POLICY["max_concurrent_jobs"]["min"]
CONCURRENCY_MAX = POLICY["max_concurrent_jobs"]["max"]
