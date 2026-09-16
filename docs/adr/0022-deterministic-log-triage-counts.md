# 0022 — Deterministic log-triage arithmetic

- Status: **accepted**
- Date: 2026-09-16

## Context

The log-triage skill previously allowed Claude to supply finding counts and
percentages. Because grouping and arithmetic were both model-generated, two
valid runs over the same evidence could report different numbers, and the
displayed findings could cover only part of the log without making the
remainder explicit.

## Decision

The sandbox owns numerical truth for `log-triage-summary`. It extracts the
physical log entries, builds a deterministic count manifest with stable
pattern IDs, and passes that manifest to Claude. Claude selects and explains
patterns by ID; the runtime replaces all counts and percentages, records the
exact `total_entries`, and appends an exact `other` remainder when selected
findings do not cover the complete input.

An unmatched narrative finding is marked `unquantified` and carries null
numeric fields rather than being mislabeled as the quantified remainder.
Historical result shapes remain readable, but new executions use the versioned
skill contract and preprocessor cache identity.

## Consequences

- Repeated executions over unchanged evidence have stable numerical results.
- Model grouping can still vary semantically, but it cannot invent arithmetic.
- The UI shows the exact denominator used for percentages.
- The `other` bucket makes incomplete model coverage visible instead of hiding
  it.
- Existing completed immutable analyses are not rewritten; a fresh execution
  is required to receive the new contract.
