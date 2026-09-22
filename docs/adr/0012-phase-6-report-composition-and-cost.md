# ADR 0012 — Phase 6 ReportPlan and Deterministic Composition

> Historical decision. For the active canonical Daily Report, the
> reference-heavy ReportPlan described here was superseded by ADR 0017:
> retired provider emits narrative only, while Report Composition derives mandatory
> coverage and section order from the frozen ReportSnapshot. The generic
> ReportDocument/parser and historical-profile compatibility remain valid.

Status: implemented

## Context

Phase 5 made `SkillSnapshot` the execution contract and introduced a generic
`ReportDocument`.  The active Daily Report skill still asked retired provider to emit
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
provenance and historical inspection. Phase 7 adds a separate
`analysis-presentation-v1` export: the bridge adapts the full frozen result
to renderer-neutral blocks, so the compact reasoning export is never used as
the human-facing technical analysis.

## Dependency execution identity

`SkillSnapshot.content_hash` identifies the snapshot's own files.
`execution_hash` is the SHA-256 of the own content hash plus sorted recursive
dependency execution identities.  A root skill with unchanged own files but
a newly pinned dependency gets a new snapshot and execution hash.  Jobs,
AnalysisRuns, Reports, ReportSnapshots, cache lookup, and the job protocol
carry the execution hash where compatibility matters.

## Phase 7 completeness and evidence integrity

The active report manifest freezes a semantic coverage policy in
`ReportSnapshot`: all reportable incidents and all available AnalysisRuns
must be represented unless a skill explicitly declares otherwise. The
composition module rejects unknown, cross-report, missing, or duplicate
references without hard-coding section names. It also rejects missing or
unreadable declared screenshots. An absent screenshot list is valid; a
declared object that cannot be fetched is a retryable/permanent evidence
failure, not a successful placeholder report.

## Evidence completeness

ReportSnapshot now freezes `trigger_value`, `teams_url`, and analysis
`report_fragment` alongside the existing incident, Grafana, log, screenshot,
and provenance data.  Composition never reads mutable incident state.

## Benchmark evidence

`scripts/benchmark_report_generation.py` compares compact legacy narrative,
full ReportDocument, and ReportPlan strategies using the real retired provider CLI when
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

## Paid AI retry budget and token accounting

RabbitMQ/MinIO infrastructure retries and paid retired provider calls are separate.
Each Job persists a four-call paid budget and the bridge reserves it once
before launching the sandbox. The sandbox enforces the same limit across
structured-output retries and LOW-to-MEDIUM escalation; a redelivery cannot
reset it. If a deterministic artifact already exists, reconciliation bypasses
the budget and retired provider entirely.

All benchmark and sandbox telemetry reports uncached input, cache creation,
cache read, and `total_model_input_tokens`, where the total is the sum of all
three input categories.

## Consequences

Report layout changes remain skill-owned.  Evidence integrity, provenance,
and storage access remain application-owned.  A new downstream analysis
schema needs only a `report-fragment-v1` adapter, not changes to the Daily
Report skill or DOCX renderer.  The remaining P2 work is an always-on,
size-independent LogEvidence extractor and broader live benchmark sampling.
