# NOC Report Builder domain context

This glossary is the shared vocabulary for maintainers and coding agents.

- **Shift** — a time-bounded operational window. Its immutable identity scopes incident readiness and report snapshots.
- **Incident** — a reportable operational alert assigned to a Shift. Its trusted evidence and current AnalysisRun feed reports.
- **Alert Evidence** — frozen screenshots and Grafana metadata shown in Alerts; storage references are allow-listed from the snapshot.
- **UploadIntent** — a short-lived, server-owned upload capability. The client submits only its opaque ID at completion; bucket and object key are never client authority.
- **Log Evidence** — the immutable log object analyzed for an Incident; its checksum and filename are preserved.
- **Evidence Byte Identity** — bucket, object key, MinIO version ID, SHA-256, content type, filename, and byte size captured when evidence is completed.
- **Evidence Retention / Purge** — referenced evidence is protected; an unreferenced deletion first commits a `PURGE_PENDING` tombstone, then removes the exact object version and records `PURGED`.
- **AnalysisRun** — one execution or cache reuse of an analysis Skill against log evidence. Only the current run is selected for a new snapshot.
- **Skill** — a named analysis/report capability with its input contract, schema, and policy.
- **SkillSnapshot** — immutable Skill content identified by hash. Jobs execute the snapshot stamped on their Job row.
- **ReportFragment** — compact bilingual analysis data sent to Daily Report reasoning to control AI usage; it is not the complete human export.
- **AnalysisPresentation** — deterministic full-fidelity rendering data derived from stored AnalysisRun output, including every required finding.
- **ReportPlan** — Claude’s narrative-only bilingual shift summary and optional cross-incident reasoning. It does not repeat mandatory incident or AnalysisRun references.
- **ReportSnapshot** — frozen Shift, Incident, evidence, AnalysisRun, provenance, report policy, and IANA operational timezone for one report generation.
- **ReportDocument** — renderer-neutral semantic blocks consumed by both Web Preview and DOCX. It separates visible metadata from audit provenance.
- **Job** — the durable unit processed through the outbox/RabbitMQ pipeline. Its AI usage budget is cumulative across deliveries and retries.
- **Job Protocol** — the versioned RabbitMQ wire contract and topology in `packages/contracts/job_message.schema.json` and `job_protocol.json`, consumed by both API and bridge.
- **AI Usage Budget** — the atomic per-Job paid-call reservation/consumption fence. `used` never decreases and `used + active reservation` never exceeds the configured budget.
- **Report Composition** — the deterministic authority that derives coverage from the frozen snapshot, validates narrative requirements, controls canonical section order, and materializes the final ReportDocument.
- **OCRExtraction** — persisted PaddleOCR output containing raw and normalized text, line confidence/coordinates, engine metadata, and screenshot provenance.
- **IncidentPrefill** — review-only, schema-validated suggestions built from OCR evidence before an Incident exists.
- **Prefill Evidence** — the exact OCR source line attached to each suggestion; unsupported model values are rejected.
- **Local Inference** — the optional single-concurrency Ollama adapter for lightweight OCR semantic mapping, separate from Claude reasoning.
- **Operational Health** — dependency checks for PostgreSQL, RabbitMQ, MinIO, the Claude bridge, and optional Ollama. Unknown or failed dependencies are never displayed as healthy.
- **Artifact Identity** — the immutable bucket, object key, MinIO version, checksum, size, and content type that identify one generated report artifact.
- **Execution Policy** — the shared model, effort, timeout, budget, and bridge-capacity contract validated by both API and bridge.
- **Bridge Capacity** — the effective number of Claude jobs the host bridge can execute concurrently. The current synchronous callback is deliberately serial (`1`).
- **Resource Scope** — this installation is a shared NOC workspace: RBAC controls actions, while incidents, evidence, analyses, and reports are team-visible resources. It is not strict per-user tenancy.
- **Dashboard Summary** — the authoritative current-Shift PostgreSQL aggregate consumed by the operator Dashboard; it is not derived from a paginated Incident response.
- **Report Artifact Identity** — the exact MinIO version, checksum, size, and content type for a generated DOCX or structured ReportDocument preview.
- **Dashboard Analysis Semantics** — an active log is awaiting analysis until its current AnalysisRun is completed with a usable result; queued/running/failed runs remain actionable, while only processing runs count as running.
- **Generated Report** — a current-Shift Report whose Job is completed and whose DOCX artifact has a durable MinIO version identity; queued, failed, and incomplete requests are not generated reports.
