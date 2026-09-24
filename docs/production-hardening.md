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
percentages after inference. Correlated occurrence counts remain available
separately, and a compact unassigned bucket preserves complete total-entry
accounting. The physical pattern manifest remains an
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
- Each worker exposes `/health/live` (process only) and `/health/ready`.
  Readiness requires the assigned RabbitMQ consumer lifecycle, Hermes gateway
  and assigned profile policy, PostgreSQL (`SELECT 1`), and the worker's
  required MinIO bucket reachability via bounded `HEAD` requests. Dependency
  probes are bounded/cached and never enumerate or mutate storage. Bucket
  `HEAD` does not prove every object-level read/write permission; real job
  operations remain authoritative and fail through existing retry/settlement
  handling if those permissions are wrong. Container healthchecks use
  readiness, while liveness stays green during dependency outages so the
  process can recover.
- Hermes and the AI Worker are optional to core readiness. Their degraded
  state is reported separately so incidents, evidence, and historical reports
  remain usable during a provider/runtime outage.
- Optional Ollama is reported as `disabled` when the feature flag is off; it
  does not turn a healthy installation into `unknown`. RabbitMQ reachability
  and queue/DLQ pipeline state remain separate details.

The web dashboard polls dependency health every 30 seconds. `unknown` is a
neutral state, not a green state.

The Admin Runtime view reads one aggregated API response. `disabled` means a
feature gate is off; `not_required` means no worker is expected; `not_configured`
means a requested feature has no configured runtime; `degraded` means the
process is reachable but not ready; `unavailable` means the dependency could
not be reached; and `unknown` means the response could not be classified.

## Hermes deployment

The log-analysis semantic path is:

```text
API → transactional outbox → RabbitMQ log_triage → AI Worker → Hermes →
validated result/telemetry artifacts → API AnalysisRun reconciliation
```

The Phase 2 Daily Alert Report path is:

```text
API → transactional outbox → RabbitMQ daily_report → Daily Report Worker →
Hermes noc-daily-report → validated narrative plan → deterministic
ReportDocument/DOCX renderer → versioned report artifacts → API Report
```

Each worker runs serially with RabbitMQ prefetch fixed at one; concurrency is
not a mutable runtime setting. The log worker consumes only `log_triage` and
verifies `noc-log-analysis`; the
Daily Report worker consumes only `daily_report` and verifies
`noc-daily-report`. The latter is an opt-in Compose profile and has its own
health port and `DAILY_REPORT_HERMES_TIMEOUT_SECONDS`. Hermes always
bootstraps the required log profile and provisions the Daily profile only
when `DAILY_REPORT_AI_ENABLED=true`. A Daily profile provisioning failure is
reported and leaves the Daily worker not-ready while the shared gateway can
still serve log analysis. The API and Compose must use the same feature-gate
value; a Hermes process/runtime failure remains a shared failure domain. Each
worker claims the PostgreSQL Job lease, renews that lease during long inference,
verifies the exact evidence version and SHA-256, materializes the immutable
SkillSnapshot, and acknowledges only after durable artifact persistence and a
conditional Job completion update. Temporary Hermes/provider failures use the
existing bounded RabbitMQ retry path; authentication, missing-skill,
evidence-integrity, and invalid-output failures are surfaced as bounded job
failures/DLQ records.

The development Compose file binds worker diagnostics to loopback for a
host-run API and therefore runs one replica per service. A deployed Compose
configuration should remove those host port bindings and point the API health
URLs at the internal service names; the worker services have no fixed
container names and can then be scaled independently.

Compose mounts `skills/` read-only into both the worker and Hermes. Hermes is
on an internal network with no published API port; only the worker joins both
the application network and the Hermes network. The Hermes container has no
Postgres, MinIO, RabbitMQ, or Docker-socket mount. The worker is the only
component holding application credentials, and Hermes provider credentials
remain inside the Hermes profile/setup.

The worker configuration source of truth is deployment environment/Compose,
not the database:

| Control | Owner / source |
| --- | --- |
| Worker kind | `RUNTIME_WORKER_KIND` (`log_triage` or `daily_report`); Compose assigns one kind per service. |
| Queue | Derived from worker kind; no separate queue override. |
| Hermes profile | One `RUNTIME_HERMES_PROFILE` per worker, constrained to the profile matching its worker kind. |
| Concurrency | Fixed to one in the consumer (`basic_qos(prefetch_count=1)`); not configurable. |
| Lease | `RUNTIME_LEASE_SECONDS`, forwarded to both Compose workers (default 900 seconds). |
| Hermes timeout | In-container `RUNTIME_HERMES_TIMEOUT_SECONDS`; Compose host override is `LOG_HERMES_TIMEOUT_SECONDS` for log analysis and `DAILY_REPORT_HERMES_TIMEOUT_SECONDS` for reports (both default 900 seconds). |
| Feature gate | `AI_RUNTIME=hermes` enables log analysis; `DAILY_REPORT_AI_ENABLED` must match in API and Hermes Compose. `--profile daily-report` separately starts the optional report worker. |
| Health port | `RUNTIME_HEALTH_PORT`; development Compose assigns 8092 to log and 8093 to Daily. |

Each worker also needs `RUNTIME_HERMES_BASE_URL` and `RUNTIME_HERMES_API_KEY`.
There is no database-backed worker timeout/capacity control: the retired
`/admin/system-config` endpoint and `system_config` table did not affect
execution. A new migration removes that unused table. Set
`AI_WORKER_HEALTH_URL` and `DAILY_REPORT_WORKER_HEALTH_URL` to the workers'
internal readiness endpoints when the API runs in a container. Do not set
provider credentials such as API keys in FastAPI or browser configuration. Pin
`HERMES_IMAGE` and record the deployed image/version in the worker's
`RUNTIME_HERMES_VERSION` for provenance.

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
Only `development`, `test`, and `production` are accepted environment values.
Production startup rejects known development JWT, PostgreSQL, RabbitMQ, MinIO,
and Hermes credentials and requires HTTPS CORS origins. Each AI worker remains
separately deployed, least-privileged, and serial (`prefetch_count=1` per
worker); scale each workload by adding its own worker replicas.

For production, set `ENVIRONMENT=production` for FastAPI and
`RUNTIME_ENVIRONMENT=production` for each worker. Configure `JWT_SECRET` with
at least 32 random characters, deployment-owned `DATABASE_URL`,
`RABBITMQ_URL`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY`, explicit HTTPS
`CORS_ORIGINS`, and a random `HERMES_API_KEY` of at least 32 characters.
Workers also need `RUNTIME_DATABASE_URL`, `RUNTIME_RABBITMQ_URL`, and
`RUNTIME_MINIO_*` credentials. `AI_RUNTIME=hermes` enables the log-analysis
queue gate; `DAILY_REPORT_AI_ENABLED=true` separately enables report jobs.
Provider credentials stay inside Hermes. Never put them in FastAPI or browser
configuration. Runtime polling is performed only while the Admin Runtime tab
is active (and the page visible); Storage bucket totals are loaded only when
the Storage tab opens and are refreshed on demand to avoid unnecessary full
object listings. The storage endpoint still computes object totals with a live
listing; if the dataset grows enough to make this costly, trustworthy MinIO
admin telemetry/metrics can replace that calculation without adding a second
monitoring stack.

Expired open upload intents are swept by the existing evidence purge worker.
The sweep locks each intent against completion, reconstructs and verifies its
server-owned bucket/key identity, skips any object referenced by Evidence,
then deletes only versions and delete markers whose key exactly matches that
intent. A successful or already-missing object becomes `CLEANED`; a storage or
database error leaves the intent available for retry. A mismatched identity
is logged and never touched.

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

CI boots the pinned Hermes container with a temporary API key and no provider
credentials. It checks listener health, auth rejection, both NOC profile
toolset policies, and chat request validation with a deliberately malformed request;
no inference/provider call is made. Unit/integration suites continue using
mocked Hermes responses.
