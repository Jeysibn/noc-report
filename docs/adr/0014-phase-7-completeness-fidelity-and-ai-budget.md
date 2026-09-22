# ADR 0014 — Phase 7 Report Completeness, Fidelity, and AI Budget

Status: implemented

## Decision

The Daily Report skill emits a semantic `ReportPlan`. Its manifest declares
coverage policy (`incidents: all`, `analyses: all_available` for the active
skill). `compose_report(plan, snapshot)` is the single test and enforcement
surface: it validates reference existence, report ownership, duplicate policy,
and required coverage before creating a `ReportDocument`.

The model receives compact `report-fragment-v1` reasoning exports. Final
technical analysis is built separately from the full frozen AnalysisRun
result through `analysis-presentation-v1`. The current Log Triage adapter
preserves all Key Finds, Secondary Finds, counts, percentages, details,
likely cause, and recommended action. A future skill can provide generic
`report_presentation` blocks; activation validation requires either that
shape or a registered presentation adapter.

Screenshot storage remains application authority. A screenshot block can be
created only from frozen snapshot evidence. Empty frozen evidence is valid;
failure to retrieve a declared object is retryable for transient storage
errors and terminal for permanent loss/corruption. The DOCX renderer never
turns either into a successful silent omission.

Phase 8 deepens this contract with the `noc-daily-report-v1` composition
profile: Alert and Log Analysis numbering/order come from the frozen snapshot,
Alerts flow across any number of pages, and each analysis block repeats its
trusted screenshot and exact frozen log filename.

Each Job has a persisted paid retired provider-call budget separate from infrastructure
attempts. The bridge atomically reserves the budget before sandbox launch,
passes it into the sandbox, persists actual calls separately from the
reservation fence, and the sandbox counts structured-output and escalation
calls in one bounded context. Artifact reconciliation avoids a new retired provider call
after a downstream finalization failure whenever the durable ReportPlan or
final artifact is already available.

## Consequences

Report formats remain skill-owned, but completeness and evidence integrity
are deterministic. Full analysis fidelity no longer increases Daily Report
prompt/output size. Skills that declare an export contract must either use a
known adapter or expose the corresponding stable output shape, preventing a
future skill from claiming compatibility based only on a string in YAML.
