from noc_bridge.config import RuntimeSettings, assert_runtime_secrets_are_safe
import pytest
from pydantic import ValidationError


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


def test_production_worker_rejects_development_service_credentials():
    config = RuntimeSettings(environment="production", hermes_api_key="x" * 40)
    with pytest.raises(RuntimeError, match="RUNTIME_RABBITMQ_URL"):
        assert_runtime_secrets_are_safe(config)


def test_production_worker_accepts_explicit_real_service_credentials():
    config = RuntimeSettings(
        environment="production",
        hermes_api_key="h" * 48,
        rabbitmq_url="amqp://worker:strong-rabbit-password@mq.internal:5672/",
        database_url="postgresql://worker:strong-db-password@db.internal:5432/noc_report",
        minio_access_key="worker-storage",
        minio_secret_key="strong-storage-password",
    )
    assert_runtime_secrets_are_safe(config)


@pytest.mark.parametrize("environment", ["prod", "Production", "productionn"])
def test_worker_rejects_unknown_environment(environment):
    with pytest.raises(ValidationError):
        RuntimeSettings(environment=environment)
