from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration (Milestone 8). Real values come from the
    environment / .env in every deployment; defaults here target the local
    docker-compose Postgres in `infrastructure/docker-compose.dev.yml`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Reliability mission Phase 17 (credential/permission hardening): a
    # deployment declares itself "production" explicitly via this env var.
    # Nothing under normal test/dev use needs to touch it — it exists so
    # `assert_production_secrets_are_safe` below has something to gate on
    # (never enforced in development, where every insecure default here
    # exists on purpose for a zero-config local run).
    environment: str = "development"

    database_url: str = "postgresql+psycopg2://noc:noc@localhost:55432/noc_report"
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_minutes: int = 60 * 24 * 7
    refresh_cookie_name: str = "noc_refresh"
    refresh_cookie_samesite: Literal["lax", "strict"] = "lax"

    # Milestone 9 — MinIO (S3-compatible object storage, master plan §25)
    minio_endpoint_url: str = "http://localhost:59000"
    minio_access_key: str = "noc-minio"
    minio_secret_key: str = "noc-minio-secret"
    minio_bucket_evidence: str = "noc-evidence"
    minio_bucket_reports: str = "noc-reports"
    minio_bucket_job_artifacts: str = "noc-job-artifacts"
    minio_presigned_url_expiry_seconds: int = 900

    # Milestone 11 — RabbitMQ (master plan §26)
    rabbitmq_url: str = "amqp://noc:noc-rabbit-secret@localhost:55672/"

    # Skill Runtime mission Phase 12: whether the outbox dispatcher runs as
    # a background thread embedded in this API process ("embedded", the
    # historical default — a dev convenience, see app/outbox_worker.py's
    # docstring) or is left entirely to an external `python -m
    # app.outbox_worker` process ("external"). A real deployment running
    # its own dispatcher process should set this to "external" so it isn't
    # also racing an embedded copy inside every API replica.
    outbox_mode: Literal["embedded", "external"] = "embedded"

    # Milestone 17 gap follow-up ("limits"): evidence uploads go straight
    # from the browser to MinIO via a presigned PUT URL (§25), so there's
    # no request body for the API to cap directly — this is enforced in
    # evidence.py's complete_upload by rejecting (and deleting) an object
    # whose actual uploaded size exceeds this, after the fact. Value chosen
    # by the operator (50 MB) — no master-plan-specified limit exists.
    max_evidence_upload_bytes: int = 50 * 1024 * 1024

    # Phase 12 local Incident Prefill AI. Disabled by default so OCR/manual
    # workflows remain the safe rollout path. The intended production
    # profile is a small Q4 ~3B Ollama model, CPU-only, one inference at a
    # time, with a short keep-alive so the model can leave RAM when idle.
    local_prefill_ai_enabled: bool = False
    local_prefill_provider: Literal["ollama", "fake"] = "ollama"
    local_prefill_base_url: str = "http://localhost:11434"
    local_prefill_model: str = "qwen2.5:3b-instruct-q4_K_M"
    local_prefill_context_size: int = 2048
    local_prefill_keep_alive: str = "5m"
    local_prefill_timeout_seconds: float = 45.0
    local_prefill_max_concurrency: int = 1
    local_prefill_max_queue: int = 8
    local_prefill_known_services: str = ""

    @property
    def known_prefill_services(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.local_prefill_known_services.split(",") if value.strip())


settings = Settings()

# Every literal below is one of this file's own insecure-by-design local
# defaults — never a real deployment's actual secret, so listing them here
# doesn't leak anything.
_INSECURE_DEFAULTS = {
    "jwt_secret": "dev-only-secret-change-me",
    "minio_secret_key": "noc-minio-secret",
    "rabbitmq_url": "amqp://noc:noc-rabbit-secret@localhost:55672/",
}


def assert_production_secrets_are_safe(config: Settings = settings) -> None:
    """Phase 17 hardening: every credential in this file ships with a
    convenient, publicly-known default so a fresh clone runs against the
    dev docker-compose stack with zero setup — the previous state of
    affairs meant that same convenience shipped silently into a real
    deployment too, if `environment=production` was set without also
    overriding these. Called once at API startup (see main.py's lifespan);
    raises RuntimeError rather than merely logging, since a production
    deployment running on the dev JWT secret is a "must not start" class
    of misconfiguration, not a warning."""
    if config.environment != "production":
        return

    still_default = [name for name, default in _INSECURE_DEFAULTS.items() if getattr(config, name) == default]
    if still_default:
        raise RuntimeError(
            "Refusing to start with environment=production while still using the "
            f"insecure default value for: {', '.join(sorted(still_default))}. "
            "Set real values via environment variables/.env before deploying."
        )
