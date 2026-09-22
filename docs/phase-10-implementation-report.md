# Phase 10 implementation notes

The canonical Daily Report now uses a narrative-only ReportPlan. The frozen
snapshot and `noc-daily-report-v1` composition profile provide all incidents
and available analyses, so retired provider no longer emits incident or AnalysisRun
references. Composition requires substantive Chinese and English General
Summary text and inserts `Alerts -> General Summary -> Log Analysis` with
natural Alert pagination and deterministic section breaks.

ReportSnapshot freezes the ShiftDefinition IANA timezone. The shared
`noc_bridge.time_projection` module converts UTC shift boundaries into the
operational calendar date, including DST-aware zones. A partial unique
PostgreSQL index guarantees one current AnalysisRun per Incident; migration
`f9a0b1c2d3e4` repairs legacy duplicates before creating it.

The Daily Report UI defaults both model and effort to System default / Auto.
Auto omits the override and bridge AI Usage Governance resolves current
SystemConfig values. Explicit overrides remain available. Canonical Alert
blocks hide status, service, environment, IDs, and provenance while retaining
those values in frozen/audit representations; only title, evidence, Grafana,
and exact filename are visible in the normal report.

Deterministic benchmark output from `scripts/benchmark_report_generation.py`
shows historical reference-heavy versus narrative-only plan JSON sizes:

| incidents | reference-heavy bytes | narrative-only bytes |
| ---: | ---: | ---: |
| 1 | 207 | 58 |
| 5 | 625 | 58 |
| 10 | 1,225 | 58 |

These are fixture serialization measurements, not unsupported live token or
cost savings. Live retired provider benchmarking remains explicitly gated.
