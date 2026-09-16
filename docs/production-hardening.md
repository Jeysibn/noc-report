# Production hardening runbook

## Evidence lifecycle

1. `POST /api/v1/incidents/{id}/evidence/upload-url` creates an
   `EvidenceUploadIntent` and returns only `upload_id`, a presigned URL, and
   expiry.
2. The browser uploads to the presigned URL.
3. Completion submits only `upload_id`. The API resolves the server-owned
   bucket/key, locks the intent, checks expiry, size, and MIME, streams the
   object to compute SHA-256, and stores the MinIO `version_id`.
4. Reports freeze bucket, key, version, hash, filename, MIME, and byte size.
   Renderers retrieve that exact version and verify the hash.

Completion is user-bound, idempotent for the same intent, and rejects client
storage coordinates. Apply the upload-intent Alembic migration before serving
this API.

## RabbitMQ job lifecycle

`packages/contracts/job_protocol.json` owns job types, exchanges, retry TTL,
and the events queue. `job_message.schema.json` owns the message shape and
requires protocol version 1. The API validates before persisting an outbox
payload; the bridge validates before reading job fields and DLQs malformed or
unsupported messages. Messages contain references, never evidence bytes.

## Operational health

- `/health` is process liveness only.
- `/health/dependencies` checks PostgreSQL, RabbitMQ (including ready/DLQ
  counts), MinIO, the bridge heartbeat endpoint, and optional Ollama.
- `/health/readiness` returns HTTP 503 when PostgreSQL, RabbitMQ, or MinIO is
  unavailable.

The web dashboard polls dependency health every 30 seconds. `unknown` is a
neutral state, not a green state. The bridge endpoint is bound to loopback by
default.

## Security and deployment

The API is an application-tier service. The host-side Claude bridge is a
privileged worker boundary that can access Docker and the Claude credential;
the sandbox remains non-root, capability-restricted, resource-limited, and
network constrained. PostgreSQL, RabbitMQ, and MinIO are infrastructure
dependencies and must use deployment-specific credentials. Production startup
rejects the known development secret defaults.

Set `CORS_ORIGINS` to the explicit comma-separated HTTPS web origin(s) in a
deployed environment; the local Vite origins are development defaults only.

Python installs use `apps/api/constraints.txt` and `bridge/constraints.txt` in
addition to their readable requirements files. The frontend uses the checked-in
root `package-lock.json`; use `npm ci` for reproducible installs.

## Verification

Run migration and component checks sequentially against the local stack:

```bash
(cd apps/api && alembic upgrade head && pytest tests/ -q)
(cd bridge && PYTHONPATH=. pytest tests/ -q)
PYTHONPATH=sandbox python3 -m pytest sandbox/tests/ -q
npm run check:coherence
(cd apps/web && npm ci && npm run typecheck && npm run lint && npm test && npm run build)
```

Live Claude verification is optional and excluded from ordinary CI.
