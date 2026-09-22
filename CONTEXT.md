# NOC Report Builder domain context

This glossary is the shared vocabulary for maintainers and coding agents.

- **Shift** — a time-bounded operational window. Its immutable identity scopes incident readiness and report snapshots.
- **Incident** — a reportable operational alert assigned to a Shift. Its trusted evidence and current AnalysisRun feed reports.
- **Alert Evidence** — frozen screenshots and Grafana metadata shown in Alerts; storage references are allow-listed from the snapshot. The renderer presents screenshots compactly without upscaling or changing their aspect ratio.
- **Alert Navigation** — renderer-owned internal DOCX links near the beginning of Alerts, targeting deterministic, collision-safe bookmarks on corresponding Log Analysis blocks. Alerts without a destination do not receive a dead link.
- **UploadIntent** — a short-lived, server-owned upload capability. The client submits only its opaque ID at completion; bucket and object key are never client authority.
- **Log Evidence** — the immutable log object analyzed for an Incident; its checksum and filename are preserved.
- **Evidence Byte Identity** — bucket, object key, MinIO version ID, SHA-256, content type, filename, and byte size captured when evidence is completed.
- **Evidence Retention / Purge** — referenced evidence is protected; an unreferenced deletion first commits a `PURGE_PENDING` tombstone, then removes the exact object version and records `PURGED`.
- **AnalysisRun** — one execution or cache reuse of an analysis Skill against log evidence. Only the current run is selected for a new snapshot.
- **Skill** — a named analysis/report capability with its input contract, schema, and policy.
- **SkillSnapshot** — immutable Skill content identified by hash. Jobs execute the snapshot stamped on their Job row.
- **ReportFragment** — compact bilingual analysis data sent to Daily Report reasoning to control AI usage; it is not the complete human export.
- **AnalysisPresentation** — deterministic full-fidelity rendering data derived from stored AnalysisRun output, including every required finding.
- **LogAnalysisResult** — the active versioned bilingual result contract: one
  exact matching-log-entry total, one shared finding identity/count/percentage/
  classification object with Chinese and English labels/details, plus concise
  summaries and severity/confidence metadata. New operator-facing output has
  Chinese first, then `Short Summary`, `Key Finds`, and `Secondary Finds` in
  both languages. Key Finds carry the substantive evidence; Secondary Finds
  remain brief. Cause/action fields are legacy compatibility only.
- **ReportPlan** — the future runtime’s narrative-only bilingual shift summary. It does not repeat mandatory incident or AnalysisRun references and does not define report layout or navigation.
- **ReportSnapshot** — frozen Shift, Incident, evidence, AnalysisRun, provenance, report policy, and IANA operational timezone for one report generation.
- **ReportDocument** — renderer-neutral semantic blocks consumed by both Web Preview and DOCX. Its active Daily Alert Report structure is Alerts, General Summary, then Log Analysis; it separates visible metadata from audit provenance.
- **Job** — the durable unit processed through the outbox/RabbitMQ pipeline. Phase 1 `log_triage` jobs are consumed by the separate AI Worker when `AI_RUNTIME=hermes`; Daily Alert Report jobs remain outside the Hermes milestone gate.
- **Job Protocol** — the versioned RabbitMQ wire contract and topology in `packages/contracts/job_message.schema.json` and `job_protocol.json`, consumed by the API and runtime-support package.
- **Report Composition** — the deterministic authority that derives coverage from the frozen snapshot, validates narrative requirements, controls canonical section order, and materializes the final ReportDocument.
- **OCRExtraction** — persisted PaddleOCR output containing raw and normalized text, line confidence/coordinates, engine metadata, and screenshot provenance.
- **IncidentPrefill** — review-only, schema-validated suggestions built from OCR evidence before an Incident exists.
- **Prefill Evidence** — the exact OCR source line attached to each suggestion; unsupported model values are rejected.
- **Local Inference** — the optional single-concurrency Ollama adapter for lightweight OCR semantic mapping, separate from the future external runtime.
- **Operational Health** — dependency checks for PostgreSQL, RabbitMQ, MinIO, the AI runtime boundary, and optional Ollama. Unknown or failed dependencies are never displayed as healthy.
- **Artifact Identity** — the immutable bucket, object key, MinIO version, checksum, size, and content type that identify one generated report artifact.
- **Execution Policy** — the provider-neutral timeout and worker-capacity contract validated by the API and runtime-support package.
- **Runtime Capacity** — the effective number of asynchronous jobs a future runtime worker may execute concurrently. The current infrastructure default is deliberately serial (`1`).
- **Resource Scope** — this installation is a shared NOC workspace: RBAC controls actions, while incidents, evidence, analyses, and reports are team-visible resources. It is not strict per-user tenancy.
- **Dashboard Summary** — the authoritative current-Shift PostgreSQL aggregate consumed by the operator Dashboard; it is not derived from a paginated Incident response.
- **Report Artifact Identity** — the exact MinIO version, checksum, size, and content type for a generated DOCX or structured ReportDocument preview.
- **Dashboard Analysis Semantics** — an active log is awaiting analysis until its current AnalysisRun is completed with a usable result; queued/running/failed runs remain actionable, while only processing runs count as running.
- **Generated Report** — a current-Shift Report whose Job is completed and whose DOCX artifact has a durable MinIO version identity; queued, failed, and incomplete requests are not generated reports.
- **Renderer Profile** — the immutable renderer contract captured in a SkillSnapshot. `report-document-v1` requires DOCX, structured preview JSON, and its screenshot index; legacy `daily_report_docx` requires DOCX only.
- **Report Capability** — `previewable` and `downloadable` are separate public capabilities. Preview requires structured ReportDocument identity plus `report.read`; download requires pinned DOCX identity plus `report.download`.
- **Historical Report Compatibility** — generated report artifacts, snapshots, and their frozen renderer profiles are immutable. Legacy documents may retain their original structure, while newly generated Daily Alert Reports omit Cross-incident Findings.
- **Poison Message** — a RabbitMQ delivery that cannot pass JSON, schema, format, protocol, or semantic identity validation. It is quarantined/DLQ'd and acknowledged without stopping the worker infrastructure.
- **Report Lifecycle State** — the public report state is `Job.status`; the `reports` table no longer stores a duplicate lifecycle status.
- **Job Lease / Redelivery** — PostgreSQL is the ownership authority while RabbitMQ retains the unacknowledged delivery. A live lease is temporary contention and is delayed through the retry queue; an expired lease is reclaimed; a completed duplicate is ACKed without re-execution.
- **Outbox Retention** — only published events older than `OUTBOX_RETENTION_DAYS` are eligible for bounded cleanup. `published_at IS NULL` rows remain pending work and are never retention-cleaned.
- **Operator Remote-Read State** — Dashboard Current Shift and Log Analysis history expose loading, success/empty, and failure states separately. Polling failures preserve the last-known-good analysis and show stale/retrying status instead of “Not analyzed.”
- **Hermes Runtime** — the provider-independent external reasoning service. It receives a prepared, untrusted-data analysis request and cannot access PostgreSQL, MinIO, RabbitMQ, application JWTs, or mutable repository skills.
- **AI Worker** — the thin application-side RabbitMQ consumer that claims leases, verifies immutable evidence and SkillSnapshot identity, performs deterministic preprocessing, calls Hermes, validates/reconciles structured output, and writes durable job artifacts.
- **Phase 1 Gate** — Hermes log analysis must pass multiple representative real-log evaluations through UI → RabbitMQ → worker → Hermes → validation → AnalysisRun → UI before Daily Alert Report integration begins.
