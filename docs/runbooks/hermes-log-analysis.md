# Hermes log-analysis runbook

## Start

From the repository root, set a strong internal key and start the Phase 1
services:

```bash
export HERMES_API_KEY="$(openssl rand -hex 32)"
docker compose -f infrastructure/docker-compose.dev.yml up -d \
  postgres minio rabbitmq hermes ai-worker
```

The repository pins the verified Hermes image digest for `v0.21.4`
(`2026.9.21`). Override `HERMES_IMAGE` and set `HERMES_VERSION` together when
performing an intentional runtime upgrade.

Run the API separately with `AI_RUNTIME=hermes` after applying migrations and
seeding the database. The local Compose file does not publish Hermes port
8642. Check the worker at `http://localhost:8092/health` and check Hermes from
inside the Compose network at `/health`.

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
[external skill directory](https://github.com/NousResearch/hermes-agent/blob/main/docs/user-guide/skills.md)
support. Do not edit the mounted skill from the container. Commit skill
changes in Git, create a new immutable SkillSnapshot, and redeploy atomically.

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

## Troubleshooting

- `AI_RUNTIME_UNAVAILABLE`: API is still using its safe default; set
  `AI_RUNTIME=hermes` and restart the API.
- Hermes health failure: inspect `docker logs noc-report-hermes`; confirm the
  internal key matches `RUNTIME_HERMES_API_KEY` and that profile/provider setup
  completed.
- `GET /v1/skills` returning a Hermes-side 500 is not used by the NOC worker;
  verify the read-only mounted skill and frozen SkillSnapshot instead. Track
  the installed Hermes image/version before upgrading past this known runtime
  compatibility issue.
- Worker health failure: inspect `docker logs noc-report-ai-worker`; verify
  Postgres, MinIO, RabbitMQ, and the Hermes network are reachable.
- Invalid output: the job is not marked successful. Inspect the bounded job
  error/DLQ record; never expose raw malformed model output to operators.
- Checksum/version failure: treat the job as an evidence identity problem;
  do not rerun against a different object version.

## Quality gate

Phase 2 is blocked until multiple representative real logs have passed the
complete UI → RabbitMQ → AI Worker → Hermes → validation → AnalysisRun → UI
flow. Evaluate Java exceptions, HTTP failures, dependency timeouts, database
and third-party failures, authentication errors, repeated high-volume errors,
multiple clusters, and mostly-INFO noise. Confirm Chinese translation,
main-error selection, concise operational language, no unsupported causal
claims, and exact deterministic counts.
