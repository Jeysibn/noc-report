"""Reliability mission Phase 17: refuse to start with environment=production
while any credential is still at its known-insecure local-dev default."""
import pytest

from app.core.config import Settings, assert_production_secrets_are_safe


def test_development_environment_never_raises_regardless_of_defaults():
    config = Settings(environment="development")
    assert_production_secrets_are_safe(config)  # must not raise


def test_production_with_all_defaults_still_in_place_raises():
    config = Settings(environment="production")
    with pytest.raises(RuntimeError, match="jwt_secret"):
        assert_production_secrets_are_safe(config)


def test_production_with_real_secrets_overridden_does_not_raise():
    config = Settings(
        environment="production",
        jwt_secret="a-real-randomly-generated-secret",
        database_url="postgresql+psycopg2://real-user:real-pass@postgres.internal:5432/noc_report",
        minio_access_key="real-minio-user",
        minio_secret_key="a-real-minio-secret",
        rabbitmq_url="amqp://real-user:real-pass@rabbitmq.internal:5672/",
        cors_origins="https://noc.internal.example",
    )
    assert_production_secrets_are_safe(config)  # must not raise


def test_production_with_only_one_default_left_names_it():
    config = Settings(
        environment="production",
        jwt_secret="a-real-randomly-generated-secret",
        database_url="postgresql+psycopg2://real-user:real-pass@postgres.internal:5432/noc_report",
        minio_access_key="real-minio-user",
        minio_secret_key="a-real-minio-secret",
        cors_origins="https://noc.internal.example",
        # rabbitmq_url left at its insecure default on purpose.
    )
    with pytest.raises(RuntimeError, match="rabbitmq_url"):
        assert_production_secrets_are_safe(config)
