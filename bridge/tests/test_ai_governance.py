from __future__ import annotations

import pytest

from noc_bridge.ai_governance import MAX_PAID_AI_CALLS_PER_JOB, effective_effort


def test_effort_policy_uses_system_default_until_explicitly_overridden():
    assert effective_effort(None, "low") == "low"
    assert effective_effort("medium", "low") == "medium"


def test_effort_policy_rejects_unknown_tiers():
    with pytest.raises(ValueError, match="unsupported AI effort"):
        effective_effort(None, "expensive")


def test_paid_budget_is_one_job_level_limit():
    assert MAX_PAID_AI_CALLS_PER_JOB == 4
