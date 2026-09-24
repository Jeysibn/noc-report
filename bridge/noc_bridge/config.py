"""Provider-neutral runtime support configuration.

This package contains the provider-neutral protocol, storage, validation, and
runtime settings used by the separately deployed AI Worker. Provider
credentials remain inside Hermes; this package only holds the worker's
application and internal-runtime connection settings.
"""
from __future__ import annotations

import pathlib
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNTIME_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    worker_kind: Literal["log_triage", "daily_report"] = "log_triage"
    rabbitmq_url: str = "amqp://noc:noc-rabbit-secret@localhost:55672/"
    database_url: str = "postgresql://noc:noc@localhost:55432/noc_report"
    minio_endpoint_url: str = "http://localhost:59000"
    minio_access_key: str = "noc-minio"
    minio_secret_key: str = "noc-minio-secret"
    minio_bucket_evidence: str = "noc-evidence"
    minio_bucket_reports: str = "noc-reports"
    minio_bucket_job_artifacts: str = "noc-job-artifacts"
    skills_dir: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "skills"
    worker_id: str = "noc-ai-worker"
    lease_seconds: int = Field(default=900, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    hermes_base_url: str = "http://hermes:8642"
    hermes_api_key: str = ""
    hermes_profile: Literal["noc-log-analysis", "noc-daily-report"] = "noc-log-analysis"
    hermes_version: str = "unknown"
    hermes_timeout_seconds: float = Field(default=900.0, gt=0)
    hermes_max_output_attempts: int = Field(default=2, ge=1)
    health_host: str = "0.0.0.0"
    health_port: int = Field(default=8092, ge=0, le=65535)
    version: str = "0.2.0"

    @model_validator(mode="after")
    def profile_matches_worker_kind(self) -> "RuntimeSettings":
        expected = {
            "log_triage": "noc-log-analysis",
            "daily_report": "noc-daily-report",
        }[self.worker_kind]
        if self.hermes_profile != expected:
            raise ValueError("RUNTIME_HERMES_PROFILE must match RUNTIME_WORKER_KIND")
        return self


settings = RuntimeSettings()


def assert_runtime_secrets_are_safe(config: RuntimeSettings = settings) -> None:
    """Fail closed for production worker deployments.

    Hermes is an authenticated internal service. A missing or placeholder
    bearer key must never allow the worker to start and repeatedly send
    unauthenticated requests, especially because the Compose stack can also
    run the Hermes API on an internal 0.0.0.0 listener.
    """
    if config.environment != "production":
        return
    if not config.hermes_api_key or config.hermes_api_key in {"change-me-local-dev", "dev-only-secret-change-me"}:
        raise RuntimeError("Refusing to start the production AI worker without a real HERMES_API_KEY.")
    if len(config.hermes_api_key) < 32:
        raise RuntimeError("Refusing to start the production AI worker with a short HERMES_API_KEY.")
    rabbit = urlparse(config.rabbitmq_url)
    database = urlparse(config.database_url)
    unsafe = []
    if rabbit.password == "noc-rabbit-secret":
        unsafe.append("RUNTIME_RABBITMQ_URL")
    if database.password == "noc":
        unsafe.append("RUNTIME_DATABASE_URL")
    if config.minio_access_key == "noc-minio" or config.minio_secret_key == "noc-minio-secret":
        unsafe.append("RUNTIME_MINIO credentials")
    if unsafe:
        raise RuntimeError("Refusing to start the production AI worker with development credentials: " + ", ".join(unsafe))
