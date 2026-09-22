"""Provider-neutral runtime support configuration.

This package contains the provider-neutral protocol, storage, validation, and
runtime settings used by the separately deployed Phase 1 AI Worker. Provider
credentials remain inside Hermes; this package only holds the worker's
application and internal-runtime connection settings.
"""
from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNTIME_", env_file=".env", extra="ignore")

    environment: str = "development"
    rabbitmq_url: str = "amqp://noc:noc-rabbit-secret@localhost:55672/"
    database_url: str = "postgresql://noc:noc@localhost:55432/noc_report"
    minio_endpoint_url: str = "http://localhost:59000"
    minio_access_key: str = "noc-minio"
    minio_secret_key: str = "noc-minio-secret"
    minio_bucket_evidence: str = "noc-evidence"
    minio_bucket_reports: str = "noc-reports"
    minio_bucket_job_artifacts: str = "noc-job-artifacts"
    max_concurrency: int = 1
    skills_dir: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "skills"
    worker_id: str = "noc-ai-worker"
    lease_seconds: int = 900
    max_attempts: int = 3
    hermes_base_url: str = "http://hermes:8642"
    hermes_api_key: str = ""
    hermes_profile: str = "noc-log-analysis"
    hermes_version: str = "unknown"
    hermes_timeout_seconds: float = 900.0
    hermes_max_output_attempts: int = 2
    health_host: str = "0.0.0.0"
    health_port: int = 8092
    version: str = "0.2.0"


settings = RuntimeSettings()
