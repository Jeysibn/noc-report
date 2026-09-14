# ADR 0015: Phase 8 Canonical Daily Report Composition

- Status: Accepted
- Date: 2026-09-14

Phases 9 and 10 harden this profile’s effort policy, shift scope, provenance
boundary, bilingual presentation, and time projection without changing its
deterministic composition model.

## Decision

The active Daily Report uses composition profile `noc-daily-report-v1`.
ReportPlan is narrative-only: Claude supplies a required non-empty Chinese and
English General Summary and optional cross-incident reasoning. The application
derives mandatory Alert and Log Analysis coverage from the immutable
ReportSnapshot and materializes blocks in frozen snapshot order. The resulting
section order is always:

```text
Report Header -> Alerts -> General Summary -> Log Analysis
```

Alerts is a flowing section and is not assigned a fixed page. The only
deterministic page boundaries are after all Alerts and after General Summary.

## Evidence and analysis

Claude receives compact `ReportFragment` reasoning exports only. Full stored
AnalysisRun output is adapted through the analysis presentation contract for
human-facing rendering. This preserves all supported Key Finds, Secondary
Finds, counts, percentages, details, and technical identifiers without a new
Claude call. Trusted screenshots and exact frozen log filenames are attached
to both the Alert and corresponding Log Analysis block.

## Rendering

The generic ReportDocument renderer owns no Daily Report business ordering.
The DOCX adapter and Web Preview adapter consume the same semantic document.
DOCX links use external hyperlink relationships, and evidence retrieval errors
are classified as retryable temporary failures or terminal integrity failures.

## Reliability and cost

RabbitMQ/infrastructure retries are separate from the persisted paid Claude
budget. Jobs keep both a conservative reservation fence and actual
`paid_ai_calls_used` accounting. A durable ReportPlan is reused when
composition, rendering, or artifact upload fails after Claude has completed.

## Consequences

The Daily Report is operationally complete because Claude cannot omit required
evidence through a reference list. Missing bilingual narrative fails bounded
plan validation rather than becoming an empty summary. Future report types can
use other composition profiles without adding NOC-specific policy to generic
rendering.
