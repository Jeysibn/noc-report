from pathlib import Path

import pytest

from noc_bridge.config import BridgeSettings, assert_production_config_is_safe


def test_bridge_development_defaults_are_allowed():
    assert_production_config_is_safe(BridgeSettings(environment="development"))


def test_bridge_production_rejects_development_credentials():
    with pytest.raises(RuntimeError, match="rabbitmq_url"):
        assert_production_config_is_safe(BridgeSettings(environment="production"))


def test_bridge_production_accepts_explicit_credentials_and_files(tmp_path: Path):
    claude = tmp_path / "claude"
    creds = tmp_path / "credentials.json"
    claude.write_text("#!/bin/sh\n")
    creds.write_text("{}")
    config = BridgeSettings(
        environment="production",
        rabbitmq_url="amqp://worker:secret@rabbitmq.internal:5672/",
        database_url="postgresql://worker:secret@postgres.internal:5432/noc_report",
        minio_access_key="worker",
        minio_secret_key="secret",
        claude_binary_path=claude,
        claude_credentials_path=creds,
    )
    assert_production_config_is_safe(config)
