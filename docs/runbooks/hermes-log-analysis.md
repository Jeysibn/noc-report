# Hermes log-analysis runbook

## Start

From the repository root, set a strong internal key and start the Hermes
services:

```bash
export HERMES_API_KEY="$(openssl rand -hex 32)"
docker compose -f infrastructure/docker-compose.dev.yml up -d \
  postgres minio rabbitmq hermes ai-worker
```

The repository pins the verified Hermes image digest for `v0.21.4`
(`2026.9.21`). Override `HERMES_IMAGE` and set `HERMES_VERSION` together when
performing an intentional runtime upgrade. `HERMES_API_KEY` is required; the
Compose file intentionally has no weak development fallback.

Run the API separately with `AI_RUNTIME=hermes` after applying migrations and
seeding the database. The local Compose file does not publish Hermes port
8642. Worker health is published on loopback only at `127.0.0.1:8092` for a
host-run API and operator diagnostics. Check it with
`curl http://127.0.0.1:8092/health` and check Hermes from inside the Compose
network at `/health`.

## Manual Hermes setup boundary

Hermes provider/model setup is intentionally manual. Follow the current
official [Docker deployment](https://hermes-agent.nousresearch.com/docs/user-guide/docker),
[API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/),
and [profile](https://hermes-agent.nousresearch.com/docs/user-guide/features/profiles/)
guides inside the dedicated Hermes data/profile, and complete any
provider login or API-key/overage authorization, select the model, and record
the deployed Hermes image/version. Provider credentials stay in Hermes; they
must not be copied into FastAPI, React, RabbitMQ messages, logs, or reports.

For this Compose deployment, the profile-specific setup boundary is:

```bash
docker exec -it noc-report-hermes hermes -p noc-log-analysis model
# or, for the portal flow:
docker exec -it noc-report-hermes hermes -p noc-log-analysis setup --portal
```

The worker calls the profile-scoped API at
`http://hermes:8642/p/noc-log-analysis/v1/chat/completions`; the default
listener health endpoint remains `http://hermes:8642/health`.

The NOC profile is `noc-log-analysis`. It auto-loads the repository-controlled
`log-triage-summary` skill from a read-only mount using Hermes'
[external skill directory](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)
support. Do not edit the mounted skill from the container. Commit skill
changes in Git, create a new immutable SkillSnapshot, and redeploy atomically.
The worker also injects the exact immutable SkillSnapshot and output schema
into each request; that frozen snapshot is authoritative for the AnalysisRun.
Hermes terminal execution is configured for the Docker backend as defense in
depth, but the deployment intentionally does not mount the Docker socket and
the NOC profile disables terminal tools.

The second isolated profile is `noc-daily-report`, which auto-loads the
repository-controlled `daily-alert-report` skill:

```bash
docker exec -it noc-report-hermes hermes -p noc-daily-report model
docker exec -it noc-report-hermes hermes -p noc-daily-report status
```

The daily profile returns only a validated bilingual narrative plan. The
worker resolves incident/evidence references from the frozen `ReportSnapshot`
and renders the DOCX itself; Hermes never receives application credentials or
creates report artifacts directly.

## Verify the flow

1. Confirm `/health/dependencies` reports the AI runtime separately from core
   readiness.
2. Create an incident and attach one JSON log file.
3. Select **Analyze Log**.
4. Follow the Job through queued, processing, and completed/failed states.
5. Confirm the result is Chinese-first, followed by English, and that counts
   match the deterministic evidence statistics.
6. Inspect AnalysisRun provenance: runtime, Hermes version/profile, provider and
   model metadata, skill hash, evidence SHA-256, preprocessing version,
   schema version, duration, and token fields where Hermes reports them.

Some Hermes API responses expose the runtime/profile/model but omit the
underlying provider name. In that case `provider` is intentionally stored as
`null`; do not infer or hard-code a provider in the NOC application. Confirm
the provider in the dedicated Hermes profile when operational provenance is
needed.

## Troubleshooting

- `AI_RUNTIME_UNAVAILABLE`: API is still using its safe default; set
  `AI_RUNTIME=hermes` and restart the API.
- Hermes health failure: inspect `docker logs noc-report-hermes`; confirm the
  internal key matches `RUNTIME_HERMES_API_KEY` and that profile/provider setup
  completed.
- `PROVIDER_RATE_LIMIT` with `usage credits are required` or HTTP 429 means
  Hermes reached the configured provider but that provider account/model is
  not currently authorized for usage. Complete the provider's billing,
  overage, or subscription authorization, or select a model available to the
  account with `docker exec -it noc-report-hermes hermes -p noc-log-analysis model`.
  Do not add that provider credential to FastAPI or the browser.
- `PROVIDER_AUTHENTICATION_ERROR` with `invalid x-api-key` or HTTP 401 means
  the provider credential in the Hermes profile is invalid or expired. Repair
  it inside Hermes' profile/provider setup and restart the worker; do not copy
  the provider key into the NOC application.
- `DAILY_REPORT_RUNTIME_UNAVAILABLE`: confirm `AI_RUNTIME=hermes` and
  `DAILY_REPORT_AI_ENABLED=true` in the API environment. The feature is
  intentionally disabled by default.
- Daily report worker failures: inspect `docker logs noc-report-ai-worker` and
  verify both profiles at startup. A report job requires the
  `daily-alert-report` SkillSnapshot and one checksum-verified ReportSnapshot
  object reference.
- `GET /v1/skills` must return successfully before enabling live profile skill
  discovery. If it fails, stop at the worker's frozen SkillSnapshot path and
  pin/upgrade Hermes to a compatible image before relying on auto-loaded
  external skills.
- Worker health failure: inspect `docker logs noc-report-ai-worker`; verify
  Postgres, MinIO, RabbitMQ, and the Hermes network are reachable.
- Invalid output: the job is not marked successful. Inspect the bounded job
  error/DLQ record; never expose raw malformed model output to operators.
- Checksum/version failure: treat the job as an evidence identity problem;
  do not rerun against a different object version.

## Log-analysis quality gate

Daily Report Phase 2 should only be enabled after multiple representative real
logs have passed the complete UI → RabbitMQ → AI Worker → Hermes → validation →
AnalysisRun → UI flow. Evaluate Java exceptions, HTTP failures, dependency
timeouts, database and third-party failures, authentication errors, repeated
high-volume errors, multiple clusters, and mostly-INFO noise. Confirm Chinese
translation, main-error selection, concise operational language, no unsupported
causal claims, and exact deterministic counts.

## Daily Alert Report flow

After the gate passes, set `DAILY_REPORT_AI_ENABLED=true` on the API and use
the normal **Generate Report** action. The flow is:

```text
ReportSnapshot → outbox → RabbitMQ daily_report → AI Worker
→ Hermes noc-daily-report → validated narrative plan
→ deterministic ReportDocument/DOCX composition → Report artifacts
```

The API still owns report section order, evidence, screenshots, links,
filenames, pagination, and artifact identity. A Hermes outage affects report
generation only; existing incidents, evidence, and historical reports remain
available.
