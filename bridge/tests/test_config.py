from noc_bridge.config import RuntimeSettings, assert_runtime_secrets_are_safe


def test_development_worker_allows_empty_hermes_key():
    assert_runtime_secrets_are_safe(RuntimeSettings(environment="development", hermes_api_key=""))


def test_production_worker_rejects_missing_hermes_key():
    try:
        assert_runtime_secrets_are_safe(RuntimeSettings(environment="production", hermes_api_key=""))
    except RuntimeError as exc:
        assert "HERMES_API_KEY" in str(exc)
    else:
        raise AssertionError("production worker accepted a missing Hermes key")


def test_production_worker_rejects_short_hermes_key():
    try:
        assert_runtime_secrets_are_safe(RuntimeSettings(environment="production", hermes_api_key="short"))
    except RuntimeError as exc:
        assert "short" in str(exc)
    else:
        raise AssertionError("production worker accepted a short Hermes key")
