# ADR-0026: Poison-message quarantine, frozen renderer recovery, and report metrics

- Status: Accepted
- Date: 2026-09-18

## Context

The serial bridge must survive malformed RabbitMQ deliveries without
weakening protocol validation. Report recovery also needs to preserve the
renderer contract captured by an immutable SkillSnapshot. Finally, Dashboard
and Analytics had to share one definition of a successfully generated report.

## Decision

JSON decoding, schema validation with active format checkers, protocol checks,
UUID parsing, and queue Job-type checks form one protected delivery boundary.
Invalid JSON is published to the job-type DLQ as a bounded diagnostic envelope;
structured invalid messages use the normal DLQ envelope. The delivery is
acknowledged only after publication, and the consumer continues.

Report reconciliation reads the renderer profile from the Job's frozen
SkillSnapshot. `report-document-v1` requires DOCX plus structured preview and
screenshot-index artifacts; legacy `daily_report_docx` requires DOCX only.

The shared Generated Report predicate is `Job.status = COMPLETED` plus a
pinned DOCX version. Analytics uses completion time and presents seven
calendar dates including today. Report lifecycle is owned by Job, so the
duplicate Report status column is removed. Preview and Download are separate
capabilities with their existing `report.read` and `report.download`
permissions.

## Consequences

Poison messages remain diagnosable without taking down the worker. Historical
Jobs retain compatibility with their original renderer contract. Operational
metrics no longer count requests as successful output, and the UI cannot offer
preview for DOCX-only legacy reports or require download permission for read-
only preview.
