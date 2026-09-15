# ADR 0018: Phase 11 branch coherence gate

## Status

Accepted

## Decision

Phase 11 adds a small, dependency-free coherence gate at
`scripts/check_coherence.py`, exposed as `npm run check:coherence` and run as
its own CI job. It checks the high-risk seams that can drift independently:

- cookie-backed authentication and the absence of browser refresh-token storage;
- shared shift incident scope and frozen `shift_timezone`;
- cumulative paid-AI accounting and centralized Auto policy resolution;
- narrative-only Daily Report schema and deterministic complete coverage;
- the durable current-`AnalysisRun` partial index;
- the canonical Job message fields and domain vocabulary.

The gate is not a replacement for API, bridge, sandbox, frontend, migration,
or integration tests. It is a fast early failure for a mixed-generation
checkout. The existing CI jobs remain the behavioral release gates.

The older reference-heavy ReportPlan decisions remain historical and are
superseded for the canonical Daily Report by ADR 0017. Compatibility parsing
for historical profiles remains intentional.

## Consequences

Contract drift fails before expensive infrastructure-backed suites run. The
check is deliberately limited to stable architectural invariants so it does
not become a second implementation of runtime behavior.
