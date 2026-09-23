# ADR 0031: Hermes semantic grouping with deterministic entry reconciliation

- Status: **accepted**
- Date: 2026-09-24

## Context

The deterministic log preprocessor correctly counted physical templates, but
those templates were too close to the logger and stack-trace representation. A
single downstream failure could therefore appear as many operator findings even
when the underlying cause was the same.

## Decision

For new `log-triage-summary` executions, the worker sends Hermes normalized log
entries annotated with stable `entry_id` values and deterministic aggregate
statistics. It does not send the physical pattern manifest or family catalogue.
Hermes groups entries by semantic operational cause and returns
`evidence_entry_ids`. The worker validates that IDs exist and are not assigned
to multiple findings, expands each semantic anchor through the deterministic
cause family assigned during preprocessing, deduplicates entries sharing the
same request correlation identity, then computes exact occurrence counts and
percentages from the expanded evidence. It retains the raw physical-record
count as `physical_entry_count` and assigns stable `semantic-*` finding
identities after reconciliation.

Existing physical-pattern reconciliation remains for immutable historical
SkillSnapshots and compatibility test doubles. It is not the active Hermes
request path.

## Consequences

- Different logger layers and exception wrappers can become one clean finding.
- Counts remain deterministic and auditable; Hermes cannot invent arithmetic.
- Entry IDs provide a direct evidence trail for each persisted finding.
- A representative Hermes selection no longer undercounts a cause that appears
  through several physical templates; all equivalent family members are
  included in the persisted evidence identity.
- Unselected entries are not expanded into one finding per physical template,
  so the operator result stays concise.
- The normalized annotated log is bounded at two million characters; larger
  evidence still uses deterministic compaction and representative entries.
