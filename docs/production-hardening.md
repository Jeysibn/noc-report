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
also version-pinned on the `reports` row. Runtime support captures both versions,
hashes, sizes, and content types before marking the Job complete. Preview
requests use those exact versions and verify their hashes; a later write to a
deterministic report key cannot alter an historical preview. The DOCX and
preview are produced from the same composed document in one runtime-support execution.

For newly generated Daily Alert Reports, that shared semantic document has
exactly three main sections in order: Alerts, General Summary, and Log
Analysis. Alerts includes a navigation list and compact evidence screenshots;
eligible navigation entries and evidence headings target deterministic DOCX
bookmarks on their corresponding Log Analysis blocks. Cross-incident Findings
is not emitted. Screenshot dimensions, paired trigger/recovery layout,
bookmarks, hyperlinks, and section order are deterministic renderer concerns,
not AI formatting decisions.

Log Analysis uses the active versioned `LogAnalysisResult` contract from
`skills/log-triage-summary`: Hermes groups normalized log entries through exact
`evidence_entry_ids`, while the worker expands those semantic anchors through
deterministic cause families and computes matching physical-entry counts and
percentages after inference. The physical pattern manifest remains an
internal deterministic fallback and is not sent to Hermes. One finding object
owns both language presentations and its Key/Secondary classification. Runtime
validation rejects duplicate identities or entry assignments, missing bilingual
detail, invalid arithmetic, oversized summaries, and unsupported shared-causation
assertions before a Job can be completed. `AnalysisPresentation` converts the stored result into the same
Chinese-first Short Summary/Key Finds/Secondary Finds sequence used by both
the standalone UI and the active Daily Report DOCX. Legacy cause/action fields
remain readable only through frozen compatibility adapters. Key Find details
may carry the substantive evidence in up to three concise sentences; Secondary
Find details are limited to one brief sentence so low-priority observations do
not compete with the primary operational explanation.

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

Report crash reconciliation reads the renderer profile from the frozen
SkillSnapshot attached to the Job. The current `report-document-v1` profile
requires DOCX, browser document, and private screenshot index; the historical
`daily_report_docx` profile requires DOCX only. Runtime support reads each exact
object version, recomputes its checksum/size, and passes the same artifact
metadata contract used by normal completion before marking the Job complete.
A partial upload is not falsely completed and can continue through the
composition retry path; a redelivery with all artifacts makes no second
semantic execution. Changing the live skill manifest cannot change an old Job's
required artifact set.

The same immutability rule applies to report presentation changes. Existing
DOCX and structured preview artifact versions are never regenerated in place;
legacy reports may retain Cross-incident Findings and their original image or
navigation layout, while current reports use the active three-section
contract.

RabbitMQ deliveries pass one protected boundary for JSON decoding, object and
schema validation, UUID format checking, protocol version, and queue Job type.
Malformed JSON is quarantined as a bounded base64 diagnostic; structured
poison messages are sent to the job-type DLQ. The original delivery is ACKed
only after quarantine/DLQ publication, and the serial consumer remains alive.

Generated Report is one shared predicate used by Dashboard and Analytics:
the associated Job must be `COMPLETED` and the Report must have a pinned DOCX
version. Analytics report windows use Job completion time, falling back to the
legacy Report generated timestamp only for old rows. The incidents-by-day
series is seven calendar dates including today.

Report lifecycle state is owned by `Job.status`; the duplicate `reports.status`
column was removed by migration `6a7b8c9d0e1f`. Public Report responses expose
separate `previewable` and `downloadable` capabilities. Preview is gated by
`report.read` and structured artifact identity; download is gated by
`report.download` and exact DOCX identity.

The Dashboard Current Shift card distinguishes `loading`, an active shift, a
successful no-active-shift response, and a failed request. A failed request is
shown with a concise error and retry action; it is never rendered as “No active
shift.” Log Analysis history follows the same distinction: an initial history
failure is not “Not analyzed,” while a transient polling failure preserves the
last-known-good run and shows a non-blocking stale/retrying indicator until the
next successful poll.

## RabbitMQ job lifecycle

`packages/contracts/job_protocol.json` owns job types, exchanges, retry TTL,
and the events queue. `job_message.schema.json` owns the message shape and
requires protocol version 1. The API validates before persisting an outbox
payload; runtime support validates before reading job fields and DLQs malformed or
unsupported messages. Messages contain references, never evidence bytes.

Published outbox retention is controlled by `OUTBOX_RETENTION_DAYS` (default
30 days), `OUTBOX_CLEANUP_BATCH_SIZE` (default 500), and
`OUTBOX_CLEANUP_INTERVAL_SECONDS` (default one hour). The dispatcher removes
only rows with `published_at` set and strictly older than the configured
cutoff, in bounded, repeatable batches. Rows with `published_at IS NULL` are
pending work and are never retention-cleaned.

Runtime workers treat PostgreSQL lease ownership and RabbitMQ delivery settlement
as separate state machines. A failed claim is classified as `CLAIMED`,
`ALREADY_COMPLETED`, `LEASE_BUSY`, or `NOT_CLAIMABLE`. A live lease is
temporary contention: its delivery is NACKed without requeue so the existing
dead-letter retry queue delays it without incrementing the Job failure attempt.
If the original worker died, the delayed delivery returns after the lease
expires and is reclaimed; if the original worker completes, the duplicate is
ACKed without another semantic execution. Completed duplicates ACK immediately,
and genuinely terminal/non-claimable rows are quarantined to the job DLQ.
This closes the crash-after-claim/before-ACK window without weakening the
existing artifact reconciliation, ReportPlan reuse, SkillSnapshot identity, or
paid-AI reservation fences.

## Operational health

- `/health` is process liveness only.
- `/health/dependencies` checks PostgreSQL, RabbitMQ (including ready/DLQ
  counts), MinIO, Hermes and the AI Worker when configured, and optional Ollama.
- `/health/readiness` returns HTTP 503 when PostgreSQL, RabbitMQ, or MinIO is
  unavailable.
- Hermes and the AI Worker are optional to core readiness. Their degraded
  state is reported separately so incidents, evidence, and historical reports
  remain usable during a provider/runtime outage.
- Optional Ollama is reported as `disabled` when the feature flag is off; it
  does not turn a healthy installation into `unknown`. RabbitMQ reachability
  and queue/DLQ pipeline state remain separate details.

The web dashboard polls dependency health every 30 seconds. `unknown` is a
neutral state, not a green state.

## Hermes deployment

The log-analysis semantic path is:

```text
API → transactional outbox → RabbitMQ log_triage → AI Worker → Hermes →
validated result/telemetry artifacts → API AnalysisRun reconciliation
```

The Phase 2 Daily Alert Report path is:

```text
API → transactional outbox → RabbitMQ daily_report → AI Worker →
Hermes noc-daily-report → validated narrative plan → deterministic
ReportDocument/DOCX renderer → versioned report artifacts → API Report
```

The worker runs with RabbitMQ prefetch and Hermes concurrency set to one. It
claims the PostgreSQL Job lease, renews that lease during long inference,
verifies the exact evidence version and SHA-256, materializes the immutable
SkillSnapshot, and acknowledges only after durable artifact persistence and a
conditional Job completion update. Temporary Hermes/provider failures use the
existing bounded RabbitMQ retry path; authentication, missing-skill,
evidence-integrity, and invalid-output failures are surfaced as bounded job
failures/DLQ records.

Compose mounts `skills/` read-only into both the worker and Hermes. Hermes is
on an internal network with no published API port; only the worker joins both
the application network and the Hermes network. The Hermes container has no
Postgres, MinIO, RabbitMQ, or Docker-socket mount. The worker is the only
component holding application credentials, and Hermes provider credentials
remain inside the Hermes profile/setup.

Required runtime settings are `AI_RUNTIME=hermes` for the API,
`RUNTIME_HERMES_BASE_URL`, `RUNTIME_HERMES_API_KEY`, and the dedicated
`RUNTIME_HERMES_PROFILE` for the worker. Set `AI_WORKER_HEALTH_URL` to the
worker's internal health endpoint when the API runs in a container. Do not set provider credentials such
as API keys in FastAPI or browser configuration. Pin `HERMES_IMAGE` and record
the deployed image/version in the worker's `RUNTIME_HERMES_VERSION` for
provenance.

`DAILY_REPORT_AI_ENABLED` remains false by default. After the Phase 1
log-analysis quality gate passes, set it to `true` to allow the report route to
freeze a `ReportSnapshot` and enqueue `daily_report`. The worker validates the
`daily-alert-report` SkillSnapshot, calls only the `noc-daily-report` profile,
and rejects narrative output containing application IDs, storage coordinates,
URLs, or report layout instructions. A corrupt or incomplete report plan is
never persisted as a successful report.

The daily profile writes a durable narrative-plan artifact before rendering.
DOCX, preview JSON, and screenshot-index artifacts are uploaded only after
deterministic composition succeeds. The report row records Hermes/profile,
provider/model when returned, snapshot hash, plan hash, token usage, and
duration. A redelivered job reuses the saved plan and complete versioned
artifact set rather than invoking the provider again.

The current profile disables terminal/process execution, filesystem mutation,
browser/web/search, messaging, code execution, delegation, memory, image/TTS,
and unrelated integrations. The read-only skill mount is the filesystem
enforcement layer; the profile's disabled toolsets are the runtime policy
layer.

## Security and deployment

The API is an application-tier service. No external semantic runtime or
provider credential is mounted into it. PostgreSQL, RabbitMQ, and MinIO are
infrastructure dependencies and must use deployment-specific credentials.
Production startup rejects known development secret defaults. The AI worker
must remain separately deployed, least-privileged, and intentionally serial
(`max_concurrent_jobs=1`); RabbitMQ prefetch is not worker parallelism.

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

Normal CI uses mocked Hermes responses and does not require paid provider calls.
Live-provider verification is an opt-in/manual operation after the operator
completes Hermes profile/provider setup.
