# 0028 — Canonical bilingual Log Analysis result and presentation

- Status: **accepted**
- Date: 2026-09-22

## Context

Log Analysis had already gained deterministic count reconciliation and a
renderer-neutral adapter, but the standalone panel still rendered English and
Chinese side by side. The active validation seam also checked only JSON shape,
so duplicate finding identities, invalid percentage arithmetic, and overly
long or unsupported narrative could reach an authoritative result.

## Decision

New `log-triage-summary` executions use a versioned canonical
`LogAnalysisResult` with:

- concise bilingual summaries;
- one stable finding `id` plus deterministic `pattern_ids` per finding;
- one authoritative count and percentage shared by both languages;
- list membership as the shared Key-vs-Secondary classification;
- `severity_signal` and `confidence` as metadata;
- no cause/action fields in the active schema or operator presentation.

The sandbox derives matching physical log-entry counts and percentages from the
full evidence. Bridge semantic validation then rejects duplicate identities,
missing language detail, count/percentage disagreement, invalid zero-entry
arithmetic, summaries outside the 2–4 sentence bound, and shared-causation
claims without linking evidence. Temporal coexistence alone is not causal
evidence.

`AnalysisPresentation` remains the single deterministic adapter for the
operator hierarchy:

```text
Chinese
  Short Summary
  Key Finds
  Secondary Finds
English
  Short Summary
  Key Finds
  Secondary Finds
```

The standalone UI and the active Daily Report `ReportDocument` consume these
same semantics. The DOCX and web preview remain two renderers of the composed
document, and no second AI shortening pass is introduced.

## Compatibility

Completed AnalysisRuns, ReportSnapshots, and generated DOCX artifacts remain
immutable. Older snapshots may retain cause/action fields and the historical
report-fragment shape; compatibility adapters keep them readable. New skill
content is version 3 and the preprocessing cache identity is version 5, so a
new execution is required to receive the canonical contract.
