"""Authoritative API-side validation for Claude execution policy.

The values live in the shared contracts directory so the API and bridge
cannot silently grow different model/effort/capacity vocabularies.
"""
from __future__ import annotations

import json
from pathlib import Path

_POLICY_PATH = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "ai_execution_policy.json"
POLICY = json.loads(_POLICY_PATH.read_text())
SUPPORTED_MODELS = tuple(POLICY["supported_models"])
SUPPORTED_EFFORTS = tuple(POLICY["supported_efforts"])

TIMEOUT_MIN = POLICY["job_timeout_seconds"]["min"]
TIMEOUT_MAX = POLICY["job_timeout_seconds"]["max"]
CONCURRENCY_MIN = POLICY["max_concurrent_jobs"]["min"]
CONCURRENCY_MAX = POLICY["max_concurrent_jobs"]["max"]
BUDGET_MIN = POLICY["claude_max_budget_usd"]["min"]
BUDGET_MAX = POLICY["claude_max_budget_usd"]["max"]
