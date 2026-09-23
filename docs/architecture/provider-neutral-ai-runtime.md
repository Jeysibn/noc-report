# Provider-neutral AI runtime boundary

Phase 1 installed Hermes Agent as the external reasoning runtime for log
analysis. Phase 2 adds a separate, opt-in Hermes profile for Daily Alert
Report narrative generation. The API still defaults to `AI_RUNTIME=disabled`
and `DAILY_REPORT_AI_ENABLED=false` for safe local development; a deployment
must explicitly enable both the runtime and the report feature.

```text
React UI
   |
FastAPI
   |---------------- PostgreSQL
   |---------------- MinIO
   |
Transactional outbox
   |
RabbitMQ (`log_triage` and `daily_report`)
   |
AI Worker (claim, verify, preprocess, validate, persist)
   |
authenticated internal HTTP
   |
Hermes (`noc-log-analysis` or `noc-daily-report` profile)
   |
configured provider/model
```

Operational health reports Hermes liveness and AI Worker process health as
separate dependencies. AI degradation does not make the core API readiness
endpoint fail; PostgreSQL, RabbitMQ, and MinIO remain the core readiness gate.

The API owns application authentication/RBAC, incident and shift state,
immutable evidence identity, SkillSnapshot and ReportSnapshot provenance,
deterministic log preprocessing, and deterministic report composition. The AI
Worker owns the application-side job lifecycle and uses the existing outbox,
RabbitMQ topology, PostgreSQL lease, MinIO evidence identity, and artifact
reconciliation. Hermes owns only model/provider execution and skill-guided
reasoning; it is not given application database, object-storage, queue, or JWT
credentials.

The log-analysis request contract contains incident metadata, evidence identity,
exact aggregate statistics, normalized log entries annotated with stable
`entry_id` values, a bounded direct log excerpt, and the language order
`zh-CN` then `en`. The physical pattern manifest remains worker-only; Hermes
selects semantic cause groups through `evidence_entry_ids`, and the worker
computes exact counts and stable semantic finding identities from those entries.
The Daily Report request is smaller: it contains a shift label, ordinal incident
context, and compact frozen analysis fragments, with application IDs and exact
timestamps removed. Evidence is explicitly untrusted data. The worker verifies
each response against the frozen SkillSnapshot schema, reconciles entry IDs,
replaces model-supplied counts with deterministic values, and only then
persists the result or report artifact.

Hermes is configured with two task-specific profiles: `noc-log-analysis`
loads `log-triage-summary`, and `noc-daily-report` loads `daily-alert-report`.
Both use the repository's skills through a read-only mount, allow one active
run, and expose no general-purpose toolsets. The worker selects the profile
from the immutable job type; the model is never asked to choose a skill.

For Daily Alert Reports, Hermes returns only a bilingual narrative
`ReportPlan`. The worker validates it, stores the plan for crash recovery, and
passes it to deterministic report composition. The application still owns
Alerts → General Summary → Log Analysis order, evidence, links, screenshots,
DOCX rendering, preview artifacts, and report provenance.

Historical AnalysisRun rows and generated reports are not rewritten. Existing
DOCX and preview artifacts continue to use their stored byte identity,
checksums, and MinIO version IDs.

Skills describe the analysis/report contract and required output, not a model
vendor. Provider credentials and model choice are configured inside Hermes,
not in FastAPI or the browser. See the deployment/runbook and ADR for the
manual provider setup boundary and failure classification.

Daily Alert Report generation remains feature-gated. Enable it only after the
Phase 1 log-analysis quality gate has passed, with
`DAILY_REPORT_AI_ENABLED=true`. The worker consumes both queues but keeps the
profiles, input projections, output schemas, and provenance separate.
