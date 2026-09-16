"""Bridge configuration (master plan §27).

The bridge is a separate host-side deployable and deliberately does not
import the FastAPI package. Shared job wire/topology definitions live in
``packages/contracts``; process-local configuration remains here.
"""
from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BRIDGE_")

    rabbitmq_url: str = "amqp://noc:noc-rabbit-secret@localhost:55672/"
    database_url: str = "postgresql://noc:noc@localhost:55432/noc_report"

    minio_endpoint_url: str = "http://localhost:59000"
    minio_access_key: str = "noc-minio"
    minio_secret_key: str = "noc-minio-secret"
    minio_bucket_evidence: str = "noc-evidence"
    minio_bucket_reports: str = "noc-reports"
    minio_bucket_job_artifacts: str = "noc-job-artifacts"

    # Sandbox Security Requirements (master plan §28) — conceptual limits.
    sandbox_cpu_nano_cpus: int = 2_000_000_000  # 2 CPUs
    sandbox_mem_limit: str = "4g"
    sandbox_pid_limit: int = 128
    sandbox_timeout_seconds: int = 300

    max_concurrency: int = 1
    health_bind_host: str = "127.0.0.1"
    health_bind_port: int = 8091

    skills_dir: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "skills"

    # Real Claude Code CLI invocation (ADR 0003) — the sandbox authenticates
    # via the operator's own Claude Code subscription login (OAuth token in
    # `claude_credentials_path`), never a separate ANTHROPIC_API_KEY. Only
    # `.credentials.json` is copied into the sandbox mount, not the rest of
    # ~/.claude (§28: "use minimum required credentials/config").
    claude_binary_path: pathlib.Path = pathlib.Path.home() / ".local/bin/claude"
    claude_credentials_path: pathlib.Path = pathlib.Path.home() / ".claude/.credentials.json"
    claude_model_default: str = "claude-sonnet-5"
    claude_max_budget_usd: float = 0.50
    claude_cli_timeout_seconds: int = 280

    # Cost-optimization mission Phase 4: escalation policy for the
    # low-effort default. A log-triage-summary result that fails structural
    # checks, comes back with confidence below this threshold, or reports
    # `severity_signal: critical` with borderline confidence, is retried
    # once at `claude_effort_escalation` (see sandbox/entrypoint.py's
    # `_escalation_reason` / `_EFFORT_RANK` for the exact rule).
    claude_effort_escalation: str = "medium"
    claude_escalation_confidence_threshold: float = 0.55

    # Cost follow-up (~/claude-cli-bridge-notes): entrypoint.py's
    # compaction defaults, made tunable here rather than baked into the
    # sandbox image. Lowered from 300000/60 — a smaller prompt is the
    # actual cost lever; model tier barely moves cost/latency at small
    # prompt sizes, per that doc's measurements.
    skill_max_log_chars: int = 80_000
    skill_max_pattern_groups: int = 40

    version: str = "0.1.0"


settings = BridgeSettings()
