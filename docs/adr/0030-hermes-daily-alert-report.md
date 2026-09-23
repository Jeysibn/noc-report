# ADR 0030: Hermes Daily Alert Report narrative runtime

Status: Accepted

## Decision

Use the dedicated Hermes `noc-daily-report` profile for Daily Alert Report
narrative generation after the log-analysis quality gate. The AI Worker sends
one compact projection of the immutable `ReportSnapshot`, validates the
bilingual narrative-only `ReportPlan`, and stores it as a durable job artifact.

The NOC application remains authoritative for the snapshot, incident and
analysis coverage, evidence identity, section order, links, screenshots,
ReportDocument composition, DOCX rendering, versioned artifacts, and report
provenance. The worker owns the job lease, checksum verification, retries,
idempotent plan reuse, and the Hermes-to-renderer boundary.

## Context

The application already owns the transactional outbox, RabbitMQ, report
snapshots, immutable skills, and deterministic report renderer. A daily report
requires semantic cross-incident narrative, but allowing a model to construct
the document or select evidence would weaken auditability and could create
unsupported or fabricated report content.

## Contract

Hermes receives only a provider-neutral projection containing the shift label,
safe incident facts, and compact frozen analysis fragments. Application IDs
and exact timestamps are intentionally removed from this projection. It does not receive
database, MinIO, RabbitMQ, JWT, or session credentials. Logs and incident
strings remain untrusted evidence. Its output is exactly:

```json
{"general_summary":{"zh":"...","en":"..."}}
```

The worker rejects extra keys, missing language blocks, storage references,
URLs, application identifiers, or malformed output. Chinese precedes English in
the semantic contract and final renderer.

## Alternatives rejected

- Direct provider calls from FastAPI: this would leak provider coupling into
  application business logic and credentials.
- One shared general-purpose Hermes profile: task-specific restriction and
  provenance would be weaker.
- Hermes-generated DOCX or evidence references: the model must not control
  artifact identity, layout, or report truth.
- Passing raw logs and all storage metadata to Hermes: deterministic compact
  projections preserve cost, privacy, and reproducibility.

## Consequences

The separate profile must be configured and authenticated inside Hermes, and
the feature flag must be enabled explicitly. Report generation has an
additional validation/rendering stage, but a Hermes outage does not make the
core application unavailable. Report rows retain runtime/profile/provider/model
metadata and the immutable snapshot/plan hashes for audit.
