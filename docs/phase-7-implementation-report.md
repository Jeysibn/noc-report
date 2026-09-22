# Phase 7 Implementation Report

Status: implemented on the current reliability branch.

## Initial findings

| Review item | Classification | Evidence |
| --- | --- | --- |
| Required incident/analysis coverage | CONFIRMED | `report_composition.py` previously checked only whether each returned ID existed. |
| Compact fragment used for final analysis | CONFIRMED | `_analysis_block()` read `report_fragment.findings` and had no full-result presentation path. |
| Screenshot retrieval silently omitted images | CONFIRMED | `docx_render.py` converted `None`/fetch errors into no image or a placeholder paragraph. |
| AI retries multiplied with Job retries | CONFIRMED | Sandbox had two structured-output tries and service had three retryable Job attempts without a persisted paid-call budget. |
| Benchmark cache accounting | PARTIALLY CONFIRMED | The Phase 6 benchmark had live envelopes but reported uncached input separately from cache fields and covered one small fixture. |
| Export contract validation | PARTIALLY CONFIRMED | Runtime accepted `report_export_contract` names but did not require a usable adapter/output shape. |
| Frozen report evidence fields | ALREADY FIXED | `reports.py` already froze trigger value, Teams/Grafana URLs, filenames, screenshots, and analysis provenance. |
| Trusted storage reference resolution | ALREADY FIXED | Composition and the service-level screenshot allow-list rejected model-supplied arbitrary MinIO paths. |

## Report completeness and fidelity

The active Daily Report manifest now freezes:

```yaml
coverage:
  incidents: all
  analyses: all_available
```

`compose_report(plan, snapshot)` validates unknown references, report ownership,
duplicates, required incident coverage, and required AnalysisRun coverage. It
does not know section names. A missing required entity raises
`OutputValidationError` and no DOCX is finalized.

The compact `report-fragment-v1` remains the only analysis projection sent to
retired provider. Final rendering uses:

```text
full AnalysisRun result
  -> analysis-presentation-v1 adapter
  -> ReportDocument blocks
  -> generic DOCX adapter
```

The current adapter preserves every Key Find and Secondary Find, including
counts, percentages, details, likely cause, and recommended action. A future
analysis skill can provide generic `report_presentation.blocks`; activation
validation requires that shape or an explicitly registered adapter.

## Evidence failure behavior

An empty frozen screenshot list is valid. A declared screenshot is mandatory:

- transient MinIO/network/read errors raise `EvidenceRetrievalError` and remain retryable;
- missing/corrupt objects raise `EvidenceIntegrityError` and are terminal;
- no fetcher or arbitrary storage reference is a terminal integrity/security failure.

The bridge stores a validated Daily Report `result.json` before DOCX
composition. If evidence retrieval or DOCX upload fails after retired provider has
returned successfully, a retry reuses that durable ReportPlan and does not
buy another retired provider call.

## Paid AI retry policy

Infrastructure attempts and paid model calls are separate. Each Job has:

```text
paid_ai_call_budget = 4
paid_ai_calls_reserved
```

The bridge atomically reserves the budget once before sandbox launch. The
sandbox counts structured-output retries and LOW-to-MEDIUM escalation calls in
one context. RabbitMQ redelivery cannot reset the counter. Existing artifacts
are reconciled before reservation.

## Live benchmark

The benchmark was run with `NOC_LIVE_REPORT_BENCHMARK=1` against the installed
retired provider CLI and wrote `scripts/benchmark_report_generation_results.json`.
The token total is explicitly:

```text
total_model_input_tokens = uncached + cache_creation + cache_read
```

Representative rows from the run:

| Incidents | Strategy | Total input | Output | Calls | Cost USD | Plan bytes | DOCX bytes |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | compact legacy | 12,682 | 578 | 1 | 0.057650 | — | — |
| 1 | full ReportDocument | 13,414 | 2,184 | 1 | 0.077298 | — | — |
| 1 | ReportPlan | 30,773 | 2,192 | 1 | 0.093169 | 1,848 | 38,229 |
| 5 | compact legacy | 13,136 | 1,099 | 1 | 0.030562 | — | — |
| 5 | full ReportDocument | 15,976 | 4,117 | 1 | 0.074849 | — | — |
| 5 | ReportPlan | 51,195 | 1,884 | 1 | 0.070222 | 2,518 | 38,492 |
| 10 | compact legacy | 13,811 | 1,063 | 1 | 0.033467 | — | — |
| 10 | full ReportDocument | 19,916 | 2,944 | 1 | 0.082219 | — | — |
| 10 | ReportPlan | 40,849 | 3,384 | 1 | 0.098426 | 3,597 | 38,817 |

This sample does not justify a universal cost-savings percentage: prompt-cache
state materially changes input accounting, and the ReportPlan strategy had
lower output than the full-document strategy for the 5-incident fixture but
not lower total model input in every row. It does prove the intended
architecture: ReportPlan output is small, references are valid, screenshots
resolve through trusted application data, and the final DOCX remains
complete without asking retired provider to reproduce the stored analysis/evidence.

## Database and migration changes

- Added `jobs.paid_ai_call_budget` and `jobs.paid_ai_calls_reserved`.
- Added `analysis_runs.total_model_input_tokens`.
- Added migration chain `f3a4b5c6d7e8` through `f6d7e8f9a0b1`; the first Phase 7 migration merges the pre-existing Alembic heads so the project now has one head.
- Restored database defaults for raw bridge inserts on the existing cache flag columns.

## Tests

Added or extended coverage for:

- complete/missing/duplicate incident and analysis coverage;
- full analysis fidelity with seven Key Finds and three Secondary Finds;
- absent screenshot, temporary retrieval failure, and permanent integrity failure;
- paid call reservation and bounded structured-output retries;
- stale outbox error clearing after successful publication;
- cache-aware total input token accounting;
- dynamic report-plan benchmark fixtures at 1, 5, and 10 incidents;
- existing compact-context guards that reject URLs, storage keys, filenames, and full analysis fields from the Daily Report prompt.

## Remaining technical debt

P0: none identified in this phase.

P1: add a first-class database record for composition attempts if operators
need per-retry evidence outage telemetry; currently Job failure state and
artifact telemetry are authoritative.

P2: make LogEvidence the default input for every large log instead of the
current bounded preprocessing/grounding path; expand the live benchmark to
multiple repeated samples and confidence-quality scoring.

Future: register additional reasoning/presentation adapters only when a real
analysis skill requires them; avoid growing a universal report-layout DSL.
