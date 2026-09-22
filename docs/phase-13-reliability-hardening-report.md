# Phase 13 reliability hardening report

Date: 2026-09-17

## Executive summary

The review confirmed a small number of high-risk seam defects rather than a
need for an architectural rewrite. The main branch now treats Evidence
deletion as a database-first, retryable lifecycle; pins generated reports to
exact MinIO versions; validates retired provider execution policy from one shared
contract; reports the bridge's actual serial capacity; distinguishes broker
health from DLQ pipeline health; and fails closed for unsafe production bridge
configuration.

The installation remains a shared NOC workspace. Existing immutable snapshots,
RabbitMQ protocol, retired provider bridge, sandbox, RBAC, AI budgets, report
composition, and local OCR/AI split were preserved.

## Findings verification

| Area | Initial classification | Result |
| --- | --- | --- |
| Evidence deletion order | CONFIRMED | Implemented DB-first `PURGE_PENDING`/`PURGED` lifecycle. |
| Historical evidence retention | CONFIRMED | OCR, AnalysisRun, and ReportSnapshot references block purge. |
| Incident deletion cleanup | CONFIRMED | Uses the same evidence tombstone and exact-object cleanup path. |
| Report artifact version identity | CONFIRMED | Added MinIO `version_id`, size, content type, checksum verification. |
| AI policy validation | PARTIALLY CONFIRMED | API schemas now reject invalid values; bridge validates legacy durable rows defensively. |
| Malformed policy bridge crash/retry | CONFIRMED | Invalid policy is a terminal durable failure and does not escape the callback. |
| Bridge capacity meaning | CONFIRMED | Current synchronous consumer is explicitly serial; policy and QoS are fixed at one. |
| Resource isolation | NOT APPLICABLE | Current domain is a shared NOC workspace, not per-user tenancy; RBAC remains enforced. |
| Production defaults | PARTIALLY CONFIRMED | API checks expanded; bridge now has an explicit production fail-closed gate. |
| Disabled Ollama health | CONFIRMED | Disabled is now distinct from unknown. |
| Queue pipeline health | PARTIALLY CONFIRMED | DLQ backlog is exposed separately and degrades the compact health summary. |
| Shift Report `NO_LOG` styling | CONFIRMED | It is now an informational workflow state, not critical severity. |
| Frontend remote state | PARTIALLY CONFIRMED | Existing shared health store retained; page-specific polling was not replaced wholesale. |
| CI undeclared sandbox dependency | CONFIRMED | CI explicitly installs pytest/PyYAML and uses Python constraints for API/bridge. |

## Architecture before/after

Evidence deletion was previously `MinIO delete → DB delete → commit`. It is
now `authorize → reference check → commit PURGE_PENDING → delete exact object
version → commit PURGED`; failed storage cleanup leaves a safe retryable
tombstone. Incident deletion detaches those tombstones before deleting the
incident.

Report generation still freezes `ReportSnapshot` first. Completion now also
freezes the generated DOCX artifact's `bucket`, `object_key`, `version_id`,
SHA-256, byte size, and content type. API downloads use that exact version and
verify the checksum; overwriting the same key cannot change a completed report.

retired provider policy is now defined in
`packages/contracts/ai_execution_policy.json`, consumed by API validation and
bridge governance. The callback remains synchronous, so effective capacity is
one and RabbitMQ prefetch is not presented as parallel execution.

## Database migration

`3d4e5f6a7b8c_lifecycle_and_report_artifact_identity.py`

- adds Evidence lifecycle state and deletion timestamp;
- makes incident ownership nullable for retained evidence tombstones;
- installs the evidence lifecycle check constraint;
- adds Report artifact version, byte-size, and content-type columns;
- installs positive AI timeout/budget and serial-capacity checks.

The migration was applied to the development database and a disposable test
database. `alembic check` reports no model/migration drift.

## Security and operational impact

- A client cannot redirect Evidence completion; the existing opaque upload
  intent remains authoritative.
- Live or historical evidence cannot be physically removed while execution or
  report records depend on it.
- Report downloads cannot silently return a newer version under an old key.
- Invalid model, effort, timeout, budget, and capacity values are rejected at
  the API seam and again at the privileged bridge seam.
- Production bridge startup rejects development infrastructure credentials,
  missing retired provider binary/credential files, and non-serial capacity.
- Optional Ollama is `disabled`, not `unknown`; RabbitMQ reachability and DLQ
  pipeline degradation are distinct signals.

## Test results

| Area | Result |
| --- | --- |
| Frontend typecheck | PASS |
| Frontend lint | PASS |
| Frontend tests | PASS — 21 tests |
| Frontend build | PASS |
| Backend complete suite | PASS — 137 tests, 4 warnings |
| Evidence lifecycle/report artifact tests | PASS — included in backend suite |
| Bridge suite | PASS — 85 passed, 2 live-retired provider tests skipped |
| Sandbox suite | PASS — 56 tests |
| AI policy/config tests | PASS — API/bridge focused tests |
| Migration coherence | PASS — `alembic check` |
| Coherence gate | PASS |
| Live retired provider execution | NOT RUN — credentialed and paid path is intentionally excluded |
| 16 GB VM/resource benchmark | NOT RUN in this pass — requires representative deployment workload |

The API suite ran against `noc_report_test`, never the live development
database. RabbitMQ and MinIO were real local services; the bridge was stopped
during API tests to prevent it consuming test messages.

## Documentation

Updated repository files:

- `CONTEXT.md`
- `docs/production-hardening.md`
- `docs/adr/0025-evidence-artifact-and-bridge-capacity-hardening.md`
- this report
- CI migration/dependency setup

Updated Obsidian files:

- `03 Projects/NOC Report Builder/04 Implementation Log/Phase 13 - Production Hardening.md`
- `03 Projects/NOC Report Builder/05 Decisions/Decision Log.md`

## Remaining risks

- P1: Existing legacy Report rows created before version pinning cannot
  reconstruct an overwritten historical object. They are reconciled/pinned
  when the completed artifact is still available; an operational backup or
  explicit retention policy is required for already-overwritten legacy keys.
- P2: The bridge remains serial by design. Real parallel execution would need
  a separate capacity/lease/sandbox/resource decision.
- P2: Frontend page polling remains local to the workflows that need it; the
  shared operational-health store is the established centralized pattern.
- Future: run the documented sanitized OCR benchmark and representative
  16-GB stack memory/swap benchmark before broad local-prefill enablement.

## Log analysis count reconciliation follow-up

The log-triage preprocessor previously exposed only model-selected prominent
patterns plus one exact `Other log entries not separately classified` bucket.
For a 2,965-entry production-style log, that made 1,806 entries look
unidentified even though the arithmetic was complete. The preprocessor now
builds stable operator-facing error templates: request IDs, endpoints,
accounts, order numbers, tokens, URLs, and stack locations are normalized or
removed, while meaningful statuses and error classes remain. Each omitted
template is appended as a deterministic secondary finding. The runtime still
owns every count and percentage; retired provider supplies labels and explanations only
for the templates it discusses. The cache contract was bumped so old
under-classified results are not reused. The verified fixture produces 24
stable templates totaling 2,965 entries with no generic `other` bucket and no
one-finding-per-request output.
