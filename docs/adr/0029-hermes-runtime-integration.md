# 0029 — Hermes as the provider-independent AI runtime

- Status: **accepted for Phase 1**
- Date: 2026-09-23

## Decision

Use Hermes Agent as a separately deployed, provider-independent reasoning
runtime behind the NOC application's existing AI runtime boundary. The first
profile is `noc-log-analysis` and the first task is `log-triage-summary` only.
The application-side AI Worker consumes the existing RabbitMQ `log_triage`
job, verifies immutable evidence and SkillSnapshot identity, performs
deterministic preprocessing, calls Hermes over authenticated internal HTTP,
validates/reconciles the structured response, stores artifacts, and settles
the existing PostgreSQL Job lease.

## Preserve

The application remains authoritative for incidents, shifts, evidence identity,
checksums/version IDs, users/RBAC, PostgreSQL, MinIO, RabbitMQ, transactional
outbox, job leases/retries, SkillSnapshot, AnalysisRun, ReportSnapshot,
deterministic counts, report provenance, rendering, DOCX, and auditability.
Hermes receives prepared facts and untrusted evidence only. It does not receive
application credentials or direct access to application infrastructure.

## Rationale

This removes provider/agent execution from FastAPI while avoiding a new custom
agent loop, skill framework, provider SDK, OAuth flow, or queue system in the
NOC application. Hermes can be upgraded or pointed at a different provider
without rebuilding the API. The separate worker allows conservative serial
concurrency, bounded retries, health isolation, and provider-neutral
provenance.

## Security and operations

- Hermes is internal-only and authenticated by a strong worker-held API key.
- The dedicated profile disables general-purpose tools and mounts repository
  skills read-only.
- Provider credentials belong to Hermes, not the browser or FastAPI.
- The worker sends only the explicit versioned request contract and treats all
  log/evidence strings as untrusted data.
- Model counts are never authoritative; deterministic preprocessing owns
  numbers and the worker reconciles them before persistence.
- Core application readiness does not fail solely because Hermes is degraded.

## Rejected alternatives

- Direct provider integration in FastAPI: couples application business logic to
  one provider and expands API secret/tooling scope.
- Restoring the removed custom provider bridge: duplicates agent/runtime
  responsibilities and weakens the provider-neutral seam.
- Giving Hermes database, MinIO, RabbitMQ, or Docker-socket access: violates
  the application truth/security boundary.
- Letting Hermes generate DOCX or choose report layout: makes auditability and
  deterministic composition dependent on model output.

## Gate

Daily Alert Report integration is a separate future phase. It may begin only
after multiple representative real log files pass the complete UI → RabbitMQ
→ worker → Hermes → validation → AnalysisRun → UI flow with acceptable
Chinese-first output, exact counts, useful root-cause reasoning, and tested
prompt-injection fixtures.
