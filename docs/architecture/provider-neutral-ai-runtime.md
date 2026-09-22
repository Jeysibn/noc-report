# Provider-neutral AI runtime boundary

Phase 1 installs Hermes Agent as the external reasoning runtime for log
analysis. The API still defaults to `AI_RUNTIME=disabled` for safe local
development, while the Compose deployment enables Hermes and the separate AI
Worker. Daily Alert Report reasoning is deliberately not wired yet.

```text
React UI
   |
FastAPI
   |---------------- PostgreSQL
   |---------------- MinIO
   |
Transactional outbox
   |
RabbitMQ (`log_triage`)
   |
AI Worker (claim, verify, preprocess, validate, persist)
   |
authenticated internal HTTP
   |
Hermes (`noc-log-analysis` profile)
   |
configured provider/model
```

The API owns application authentication/RBAC, incident and shift state,
immutable evidence identity, SkillSnapshot and ReportSnapshot provenance,
deterministic log preprocessing, and deterministic report composition. The AI
Worker owns the application-side job lifecycle and uses the existing outbox,
RabbitMQ topology, PostgreSQL lease, MinIO evidence identity, and artifact
reconciliation. Hermes owns only model/provider execution and skill-guided
reasoning; it is not given application database, object-storage, queue, or JWT
credentials.

The request contract contains incident metadata, evidence identity, exact
statistics, stable pattern IDs, representative entries, a bounded excerpt,
and the language order `zh-CN` then `en`. Evidence is explicitly untrusted
data. The worker verifies the response against the frozen SkillSnapshot schema,
replaces model-supplied counts with deterministic values, and only then
persists the result artifact for the API to reconcile into `AnalysisRun`.

Hermes is configured with a dedicated `noc-log-analysis` profile, the
repository's `log-triage-summary` skill through a read-only mount, one active
run, and no general-purpose toolsets. The future `daily-alert-report` profile
is intentionally deferred until the mandatory log-analysis quality gate passes.

Historical AnalysisRun rows and generated reports are not rewritten. Existing
DOCX and preview artifacts continue to use their stored byte identity,
checksums, and MinIO version IDs.

Skills describe the analysis/report contract and required output, not a model
vendor. Provider credentials and model choice are configured inside Hermes,
not in FastAPI or the browser. See the deployment/runbook and ADR for the
manual provider setup boundary and failure classification.

Daily Alert Report generation remains feature-gated until the Phase 1 quality
gate passes. The report endpoint must not enqueue a `daily_report` job while
the Phase 1 worker only consumes `log_triage`.
