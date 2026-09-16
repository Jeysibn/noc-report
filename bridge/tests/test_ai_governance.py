from __future__ import annotations

import pytest

from noc_bridge.ai_governance import (
    MAX_PAID_AI_CALLS_PER_JOB,
    InvalidExecutionPolicy,
    effective_effort,
    effective_model,
)


def test_effort_policy_uses_system_default_until_explicitly_overridden():
    assert effective_effort(None, "low") == "low"
    assert effective_effort("medium", "low") == "medium"


def test_effort_policy_rejects_unknown_tiers():
    with pytest.raises(ValueError, match="unsupported AI effort"):
        effective_effort(None, "expensive")


def test_model_policy_uses_system_default_until_explicitly_overridden():
    assert effective_model(None, "claude-sonnet-5") == "claude-sonnet-5"
    assert effective_model("claude-opus-5", "claude-sonnet-5") == "claude-opus-5"


def test_model_policy_rejects_unknown_models():
    with pytest.raises(InvalidExecutionPolicy, match="unsupported AI model"):
        effective_model(None, "model-x")


def test_invalid_stored_capacity_is_rejected_before_dispatch():
    from noc_bridge.ai_governance import validate_system_config

    with pytest.raises(InvalidExecutionPolicy, match="capacity"):
        validate_system_config({
            "default_model": "claude-sonnet-5",
            "default_effort": "low",
            "job_timeout_seconds": 300,
            "max_concurrent_jobs": 4,
            "claude_max_budget_usd": 0.5,
        })


def test_paid_budget_is_one_job_level_limit():
    assert MAX_PAID_AI_CALLS_PER_JOB == 4
