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
storage coordinates. Evidence deletion is database-first: it commits a
`PURGE_PENDING` tombstone, then removes the exact MinIO version and records
`PURGED`. OCR runs, AnalysisRuns, and historical ReportSnapshots protect
referenced bytes; failed cleanup remains retryable and never leaves a live
Evidence row pointing at bytes that this request deleted. Incident deletion
uses the same tombstone path.

Reports also store `report_version_id`, byte size, content type, and SHA-256.
Report downloads use the pinned MinIO version and verify the checksum. Rows
created before artifact version pinning are legacy and are not downloadable
until a poll reconciles and pins their artifact.

The structured `ReportDocument` preview and its private screenshot index are
also version-pinned on the `reports` row. The bridge captures both versions,
hashes, sizes, and content types before marking the Job complete. Preview
requests use those exact versions and verify their hashes; a later write to a
deterministic report key cannot alter an historical preview. The DOCX and
preview are produced from the same composed document in one bridge execution.

Evidence that has reached `PURGE_PENDING` is retried by the embedded
`evidence-purge-worker` (or `python -m app.evidence_purge_worker`). It uses
row locking with `SKIP LOCKED`, exact evidence versions, idempotent missing
object handling, and leaves the tombstone pending after a temporary failure.
If a pending row becomes protected again, it is skipped for the current
bounded sweep so later eligible rows are still processed; the row remains
protected and pending for a future sweep.
The pending count and failures are visible in worker logs. A report snapshot
reference prevents purge from proceeding.

## Dashboard ownership

`GET /api/v1/dashboard/summary` is the authoritative current-Shift aggregate
used by the Dashboard cards. It counts directly in PostgreSQL, including
active log evidence, and does not derive values from the first page of
incidents. “Reports generated” means a completed Job whose Report row has a
pinned report artifact version; queued, processing, failed, and unpinned rows
are excluded. “Logs awaiting analysis” means active log evidence without a
current completed AnalysisRun containing a usable result; no run, queued,
running, and failed runs remain actionable. “Analyses running” counts only
currently processing runs. A failed request is shown as an error with retry
instead of fabricated zero values.

Report crash reconciliation checks the complete deterministic artifact set.
For the current ReportDocument renderer, DOCX, browser document, and private
screenshot index must all be present. The bridge reads each exact object
version, recomputes its checksum/size, and passes the same artifact metadata
contract used by normal completion before marking the Job complete. A partial
upload is not falsely completed and can continue through the composition
retry path; a redelivery with all artifacts makes no second Claude call.

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
- Optional Ollama is reported as `disabled` when the feature flag is off; it
  does not turn a healthy installation into `unknown`. RabbitMQ reachability
  and queue/DLQ pipeline state remain separate details.

The web dashboard polls dependency health every 30 seconds. `unknown` is a
neutral state, not a green state. The bridge endpoint is bound to loopback by
default.

## Security and deployment

The API is an application-tier service. The host-side Claude bridge is a
privileged worker boundary that can access Docker and the Claude credential;
the sandbox remains non-root, capability-restricted, resource-limited, and
network constrained. PostgreSQL, RabbitMQ, and MinIO are infrastructure
dependencies and must use deployment-specific credentials. Production startup
rejects the known development secret defaults. The bridge also refuses
production startup with development infrastructure credentials, missing
Claude credentials, or a non-serial capacity setting. Its callback is
intentionally serial, so `max_concurrent_jobs=1`; RabbitMQ prefetch is not
worker parallelism.

Claude OAuth credentials are copied into the bridge-owned
`~/.cache/noc-report/claude` directory by default. Claude Code may write token
refresh state there, so the directory is mode `0700` and the credential file
is mode `0600`; the full host `~/.claude` directory is never mounted. A newer
host login replaces the cache, while a refreshed cache survives bridge and
sandbox restarts. If the refresh token is revoked or expired, an interactive
`claude auth login` remains necessary.

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
