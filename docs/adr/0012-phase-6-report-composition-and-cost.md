# ADR 0012 — Phase 6 ReportPlan and Deterministic Composition

Status: implemented

## Context

Phase 5 made `SkillSnapshot` the execution contract and introduced a generic
`ReportDocument`.  The active Daily Report skill still asked Claude to emit
deterministic evidence blocks, URLs, filenames, screenshots, and private Log
Triage fields.  That increased output cost and made model output a storage
authority.

## Decision

The active report skill emits a compact `ReportPlan`.  Its allowed nodes are:

- `heading`
- `paragraph`
- `bilingual_generated_text`
- `incident_reference`
- `analysis_reference`
- `divider`
- `page_break`

The plan contains generated narrative and trusted IDs only.  It cannot emit
bucket names, object keys, URLs, filenames, copied analysis objects, or
incident metadata.

`bridge/noc_bridge/report_composition.py::compose_report` resolves the plan
against the immutable ReportSnapshot.  It creates `ReportDocument` blocks,
resolves Teams/Grafana links, trigger values, log filenames, screenshots, and
analysis provenance, and rejects unknown or cross-report references.

The DOCX adapter remains semantic-format agnostic.  The service passes it a
fetcher guarded by the frozen snapshot's `(bucket, object_key)` allow-list.
The model can therefore request only an incident or analysis reference; it
cannot authorize an arbitrary MinIO read.

Historical snapshots using `daily_report_docx` remain on the compatibility
assembler.  Historical `report-document-v1` results can still be rendered by
the generic parser; new jobs use ReportPlan composition.

## Stable analysis-to-report contract

The API freezes `report_fragment` under `report-fragment-v1` for each
AnalysisRun.  The current Log Triage adapter maps its private result once;
future analysis skills may supply a `report_context` with the same stable
shape.  Daily Report consumes only the fragment's summary, cause, action,
severity, and bounded findings.  Full analysis results remain stored for
provenance and historical inspection.

## Dependency execution identity

`SkillSnapshot.content_hash` identifies the snapshot's own files.
`execution_hash` is the SHA-256 of the own content hash plus sorted recursive
dependency execution identities.  A root skill with unchanged own files but
a newly pinned dependency gets a new snapshot and execution hash.  Jobs,
AnalysisRuns, Reports, ReportSnapshots, cache lookup, and the job protocol
carry the execution hash where compatibility matters.

## Evidence completeness

ReportSnapshot now freezes `trigger_value`, `teams_url`, and analysis
`report_fragment` alongside the existing incident, Grafana, log, screenshot,
and provenance data.  Composition never reads mutable incident state.

## Benchmark evidence

`scripts/benchmark_report_generation.py` compares compact legacy narrative,
full ReportDocument, and ReportPlan strategies using the real Claude CLI when
`NOC_LIVE_REPORT_BENCHMARK=1` is set.  The recorded run in
`scripts/benchmark_report_generation_results.json` measured:

| strategy | output tokens | cost (USD) | document/reference checks |
| --- | ---: | ---: | --- |
| compact legacy | 831 | 0.0120 | structured output |
| full ReportDocument | 2,499 | 0.0297 | structured output |
| ReportPlan | 1,927 | 0.0322 | composition, reference, screenshot, and analysis checks |

The ReportPlan output was 23% smaller than the full-document run in this
sample and, more importantly, removed deterministic evidence reproduction
from the model contract.  Token/cost values are telemetry from one live
representative run, not a pricing guarantee; cache state affects them.

## Consequences

Report layout changes remain skill-owned.  Evidence integrity, provenance,
and storage access remain application-owned.  A new downstream analysis
schema needs only a `report-fragment-v1` adapter, not changes to the Daily
Report skill or DOCX renderer.  The remaining P2 work is an always-on,
size-independent LogEvidence extractor and a larger statistical benchmark.
