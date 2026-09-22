# Phase 8 Implementation Report

Date: 2026-09-14

Reference: `2026-09-13_Morning_Shift_Report.docx`

The named DOCX is not present in the repository checkout, so visual
comparison against its pixels could not be performed. The implementation
uses the structural rules in the Phase 8 brief and verifies the generated
DOCX package, semantic block order, hyperlinks, evidence repetition, and
natural page-break behavior.

## Initial finding classification

| Finding | Classification | Evidence |
| --- | --- | --- |
| Frozen ReportSnapshot is authoritative | ALREADY FIXED | `reports.py` freezes incident facts, evidence references, analysis output, and provenance. |
| Compact reasoning export exists | ALREADY FIXED | `report_fragments.py` and the Daily Report input projection expose only compact context to retired provider. |
| Full analysis presentation export exists | PARTIALLY CONFIRMED | The adapter preserved all findings, but the final document did not repeat screenshots or log filenames in Log Analysis. |
| Coverage validation exists | PARTIALLY CONFIRMED | Coverage errors were rejected, but retired provider reference order still controlled alert numbering. |
| Canonical section order exists | ALREADY FIXED | Composition inserted Alerts, General Summary, and Log Analysis boundaries. |
| Alerts natural pagination | ALREADY FIXED | Composition inserted no page breaks between alert blocks. |
| Alert evidence block order | CONFIRMED | DOCX rendering placed metadata/links before screenshots. |
| Clickable DOCX hyperlinks | CONFIRMED | Links were rendered as ordinary URL paragraphs. |
| Frozen human-readable shift | CONFIRMED | Snapshots stored shift timestamps but not the mutable ShiftDefinition display name. |
| Expected screenshot retrieval failure | ALREADY FIXED | Retrieval and integrity errors were classified separately and retried/terminated accordingly. |
| Shared Web/DOCX semantic document | ALREADY FIXED | Both adapters consume the composed `ReportDocument`. |
| Persisted actual paid retired provider calls | PARTIALLY CONFIRMED | The Job tracked conservative reservations, but not actual calls completed by a sandbox. |
| Canonical reference artifact | NOT PRESENT | The named DOCX is not in the repository or checkout. |

## Implementation

The active report profile is `noc-daily-report-v1`. `compose_report()` now
treats plan references as validated authorization requests and materializes
the final alert and analysis blocks from the frozen snapshot order. This
guarantees deterministic numbering and prevents missing or unrelated content
from entering the document. The active manifest still requires every frozen
incident and every available frozen analysis exactly once.

The data flow is:

```text
Full AnalysisRun result ──> AnalysisPresentation (all findings)
                         └─> ReportFragment (compact retired provider context)

Frozen ReportSnapshot + compact retired provider ReportPlan + AnalysisPresentation
                         -> canonical Report Composition
                         -> ReportDocument
                            /          \
                         Web          DOCX
```

`AnalysisReference` now carries trusted screenshots and the exact frozen log
filename, so Log Analysis is self-contained and repeats the Alert evidence
without another AI call. Counts and percentages are formatted directly from
stored analysis output.

The DOCX adapter now provides reusable professional styling, margins,
header/footer and page fields, Chinese-capable font declarations, centered
title, blue incident headings, centered screenshots, keep-with-next behavior,
natural alert pagination, and true external hyperlink relationships. The
canonical Alert order is heading, screenshot, links, log filename, then
supplemental metadata. No Daily Report ordering rule was added to the generic
renderer.

The API freezes `shift_name`, `shift_code`, and `shift_display_name` in the
snapshot JSON. Later ShiftDefinition edits therefore cannot change an old
report header.

Paid AI accounting now distinguishes the reservation crash fence from
`paid_ai_calls_used`. Sandbox telemetry is returned with the job result and
recorded after sandbox completion; unused reservations are released while a
crashed worker remains fenced. A downstream retry can reuse the durable
ReportPlan and does not invoke retired provider again.

## Verification

Added/updated tests cover:

- three-or-more Alert section order and deterministic numbering despite a
  reversed retired provider reference order;
- missing and duplicate incident/analysis coverage;
- eight-plus natural multi-page Alert fixtures with only the two explicit
  section boundaries;
- seven Key Finds and four Secondary Finds with counts, percentages, and
  details preserved;
- repeated screenshots in Alerts and Log Analysis;
- proper DOCX hyperlink relationships;
- exact frozen log filename and optional log-file hyperlink support;
- browser rendering of the shared semantic evidence and analysis blocks;
- persisted paid-call accounting fields and retry separation.

Focused verification completed:

```text
27 bridge report/composition/document tests passed
18 API report/analysis tests passed
web TypeScript check passed
5 web preview/report tests passed
Python compileall passed
```

The live retired provider benchmark remains explicitly gated because it spends
subscription usage. `scripts/benchmark_report_generation.py` compares:

```text
A historical compact report prompt
B full AI ReportDocument generation
C compact ReportPlan plus deterministic composition
```

Each row records uncached input, cache creation/read input, total model input
(`uncached + cache creation + cache read`), output tokens, retired provider calls,
duration, cost, plan/DOCX size, incident completeness, analysis completeness,
and screenshot completeness. The existing checked-in live result is from the
previous Phase 7 run; no unsupported universal savings claim is made.

## Remaining issues

- P0: none identified in the implemented scope.
- P1: the named golden DOCX must be added or attached before pixel-level
  visual comparison can be performed; local conversion tooling is not
  installed in this checkout.
- P2: production MinIO/bridge integration tests require the repository's
  Postgres/RabbitMQ/MinIO test stack and latest migrations to be running.
- Future: add a first-class report-attempt/evidence-outage telemetry record if
  operators need more detail than Job failure history.
