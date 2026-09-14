# NOC Report Builder domain context

This glossary is the shared vocabulary for maintainers and coding agents.

- **Shift** — a time-bounded operational window. Its immutable identity scopes incident readiness and report snapshots.
- **Incident** — a reportable operational alert assigned to a Shift. Its trusted evidence and current AnalysisRun feed reports.
- **Alert Evidence** — frozen screenshots and Grafana metadata shown in Alerts; storage references are allow-listed from the snapshot.
- **Log Evidence** — the immutable log object analyzed for an Incident; its checksum and filename are preserved.
- **AnalysisRun** — one execution or cache reuse of an analysis Skill against log evidence. Only the current run is selected for a new snapshot.
- **Skill** — a named analysis/report capability with its input contract, schema, and policy.
- **SkillSnapshot** — immutable Skill content identified by hash. Jobs execute the snapshot stamped on their Job row.
- **ReportFragment** — compact bilingual analysis data sent to Daily Report reasoning to control AI usage; it is not the complete human export.
- **AnalysisPresentation** — deterministic full-fidelity rendering data derived from stored AnalysisRun output, including every required finding.
- **ReportPlan** — Claude’s narrative-only bilingual shift summary and optional cross-incident reasoning. It does not repeat mandatory incident or AnalysisRun references.
- **ReportSnapshot** — frozen Shift, Incident, evidence, AnalysisRun, provenance, report policy, and IANA operational timezone for one report generation.
- **ReportDocument** — renderer-neutral semantic blocks consumed by both Web Preview and DOCX. It separates visible metadata from audit provenance.
- **Job** — the durable unit processed through the outbox/RabbitMQ pipeline. Its AI usage budget is cumulative across deliveries and retries.
- **AI Usage Budget** — the atomic per-Job paid-call reservation/consumption fence. `used` never decreases and `used + active reservation` never exceeds the configured budget.
- **Report Composition** — the deterministic authority that derives coverage from the frozen snapshot, validates narrative requirements, controls canonical section order, and materializes the final ReportDocument.
