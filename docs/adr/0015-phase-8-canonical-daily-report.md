# ADR 0015: Phase 8 Canonical Daily Report Composition

- Status: Accepted
- Date: 2026-09-14

Amended 2026-09-19: the active contract removes optional cross-incident
reasoning/Findings from new reports and adds deterministic DOCX navigation and
compact screenshot layout. Historical frozen reports remain governed by the
renderer profile captured when they were generated.

Phases 9 and 10 harden this profile’s effort policy, shift scope, provenance
boundary, bilingual presentation, and time projection without changing its
deterministic composition model.

## Decision

The active Daily Report uses composition profile `noc-daily-report-v1`.
ReportPlan is narrative-only: retired provider supplies a required non-empty Chinese and
English General Summary. Cross-incident Findings is not part of the active
ReportPlan or newly composed ReportDocument. The application derives mandatory
Alert and Log Analysis coverage from the immutable
ReportSnapshot and materializes blocks in frozen snapshot order. The resulting
section order is always:

```text
Report Header -> Alerts -> General Summary -> Log Analysis
```

Alerts is a flowing section and is not assigned a fixed page. The only
deterministic page boundaries are after all Alerts and after General Summary.
The Alerts section begins with Alert Navigation, followed by the complete
evidence blocks. General Summary therefore starts only after every Alert
evidence block, and Log Analysis remains the final main section.

## Evidence and analysis

retired provider receives compact `ReportFragment` reasoning exports only. Full stored
AnalysisRun output is adapted through the analysis presentation contract for
human-facing rendering. This preserves all supported Key Finds, Secondary
Finds, counts, percentages, details, and technical identifiers without a new
retired provider call. Trusted screenshots and exact frozen log filenames remain part of
the supported evidence contract. The DOCX adapter scales screenshots within
compact width and height bounds while preserving aspect ratio and avoiding
unnecessary upscaling. Trigger/recovery pairs use a compact side-by-side layout
when readable and fall back to stacked images when required by their shape.

## Rendering

The generic ReportDocument renderer owns no Daily Report business ordering.
The DOCX adapter and Web Preview adapter consume the same semantic document.
The DOCX adapter creates deterministic, collision-safe bookmarks for eligible
Log Analysis blocks and internal hyperlinks from Alert Navigation and alert
evidence headings. Repeated or multilingual titles do not determine bookmark
identity, and alerts without a Log Analysis destination do not receive a dead
link. The adapter continues to support external hyperlink relationships, and
evidence retrieval errors are classified as retryable temporary failures or
terminal integrity failures.

## Reliability and cost

RabbitMQ/infrastructure retries are separate from the persisted paid retired provider
budget. Jobs keep both a conservative reservation fence and actual
`paid_ai_calls_used` accounting. A durable ReportPlan is reused when
composition, rendering, or artifact upload fails after retired provider has completed.

## Historical compatibility

This amendment applies only to newly generated reports. Existing Report
artifacts, structured previews, snapshots, and their captured renderer profiles
are immutable and are not recomposed. Legacy reports that include
Cross-incident Findings remain viewable/downloadable exactly as generated.

## Consequences

The Daily Report is operationally complete because retired provider cannot omit required
evidence through a reference list. Missing bilingual narrative fails bounded
plan validation rather than becoming an empty summary. Future report types can
use other composition profiles without adding NOC-specific policy to generic
rendering.
