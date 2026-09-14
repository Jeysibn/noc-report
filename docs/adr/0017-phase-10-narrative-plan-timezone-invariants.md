# ADR 0017: Phase 10 narrative plans and durable report invariants

## Status

Accepted

## Decision

For `noc-daily-report-v1`, Claude returns only narrative data: a required,
non-empty bilingual `general_summary` and optional bilingual
`cross_incident_findings`. Frozen Incident and current AnalysisRun coverage,
ordering, numbering, evidence, and full presentation are deterministic
Report Composition responsibilities. Older block/reference plans remain
available only to explicitly historical, non-canonical profiles.

The operational timezone from ShiftDefinition is copied into ReportSnapshot.
Report Composition uses the shared timezone projection module for the report's
calendar date; renderers never reinterpret UTC timestamps or mutable shift
configuration.

PostgreSQL enforces at most one `AnalysisRun.current = TRUE` row per incident
with a partial unique index. Application transitions remain explicit, and a
race is handled as a deterministic conflict rather than leaving ambiguous
snapshot selection.

The Daily Report UI represents model Auto as an omitted request override. The
bridge resolves the live SystemConfig default through AI Usage Governance;
explicit model selections remain supported. Conservative paid-AI reservations
remain unchanged because uncertain worker failure must not restore capacity.

## Consequences

Plans are smaller and have fewer structured-output failure modes. A valid plan
cannot remove mandatory facts, and report dates remain reproducible across
calendar boundaries and later configuration edits. The partial index requires
legacy duplicate-current rows to be repaired during migration; the newest row
is retained as current.
