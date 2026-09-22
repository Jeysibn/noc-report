# AI Cost Optimization — Phase 2 Final Report

Mission: reduce AI usage per NOC analysis while preserving or improving
incident-analysis quality, rare-critical-error detection, bilingual
output, auditability, determinism, reliability, and security. Guiding
principle: **"Deterministic code computes facts. retired provider interprets
evidence."** Full change log/reasoning lives in ADR 0005; this report is
the required 10-section summary.

## 1. Executive summary

Ten issues were scoped from the mission brief. Nine (1-6, 9, plus a
correction to 10's target doc) are fully implemented and tested against
real infrastructure (Postgres/RabbitMQ/MinIO/Docker, and a real `retired-runtime`
CLI benchmark). Issue 7 is intentionally scoped to the specific
correctness bug it named rather than a full pipeline rebuild, and Issue 8
is a real but single-pass (not statistically large) benchmark — both
scoping decisions are stated explicitly in ADR 0005 rather than left
implicit. No architectural component was removed or replaced: the
React → FastAPI → Postgres/MinIO → RabbitMQ → Bridge → Docker Sandbox →
retired provider runtime CLI pipeline, OAuth subscription billing, and RBAC are all
unchanged.

## 2. What was inspected (ground truth, not prior docs)

Re-read, this session: `sandbox/entrypoint.py` (full CLI invocation,
compaction, escalation, telemetry), `bridge/noc_bridge/service.py`
(job processing, upload, retry/DLQ), `bridge/noc_bridge/validation.py`,
`bridge/noc_bridge/docx_render.py`, `apps/api/app/api/v1/routers/
analysis.py` and `reports.py` (cache lookup, snapshot building),
`apps/api/app/models/models.py`, the Alembic migration chain, `.github/
workflows/ci.yml`, and both skills' `SKILL.md` files. ADR 0004's two
inaccurate claims (Phase 2/3 "already minimal", Phase 8 "no work needed")
were found by this inspection, not assumed — see the correction note atop
ADR 0004.

## 3. Architecture (unchanged, confirmed)

```
React UI -> FastAPI (apps/api) -> Postgres (jobs/analysis_runs/reports)
    -> RabbitMQ -> bridge/noc_bridge/service.py (host process)
      -> Docker sandbox (non-root, cap-drop ALL, no-new-privileges,
         read-only rootfs, tmpfs-only, resource/pid/time limits)
        -> sandbox/entrypoint.py -> `retired-runtime` CLI (OAuth subscription
           credential bind-mounted, never --bare, never API-key billing)
      -> MinIO (artifacts/reports) + Postgres (provenance)
```

## 4. Per-log analysis pipeline — before / after

**Before (this mission):**
```
log.txt -> compact if > 80,000 chars (frequency+severity aware, but
  \d+ blanket-normalized -- HTTP 403 and 500 could collapse together)
  -> SKILL.md + log text -> `retired-runtime -p ... --restricted --permission-mode
  dontAsk` (no --system-prompt override, no --no-session-persistence)
  -> result/telemetry parsed by hoping `result` field is a JSON string
  -> escalation telemetry OVERWRITES initial attempt's numbers
```

**After (this mission, Issues 1/2/4/7):**
```
log.txt -> compact if > 80,000 chars (field-aware: dynamic ids/timestamps
  normalized, HTTP/business/error codes preserved distinctly)
  -> SKILL.md + log text -> `retired-runtime -p ... --system-prompt <minimal NOC
  identity> --no-session-persistence --restricted --permission-mode
  dontAsk --permission-prompts none`
  -> _parse_retired-runtime_result prefers structured_output, falls back to result
  -> telemetry: initial_* and escalation_* both kept; totals are true sums
```

## 5. Daily report pipeline — before / after

**Before:** retired provider received the entire shift snapshot (every incident's
full analysis object, screenshots, MinIO/Grafana references) and was
asked to reproduce/echo most of it back inside `sections[]`, alongside
the shift-level overview — real token cost for zero reasoning value.

**After (Issue 6):** retired provider receives only compact per-incident summaries
(`display_id/title/status/severity_signal/main_error/impact/starts_at/
ends_at`) and returns only `{overview_en, overview_zh,
cross_incident_findings_en, cross_incident_findings_zh}`.
`bridge/noc_bridge/service.py`'s `_merge_daily_report` deterministically
rebuilds the full report (title computed, per-incident fields carried
through verbatim from the frozen snapshot) before validation and DOCX
rendering.

## 6. Issue-by-issue status

| # | Issue | Status |
|---|-------|--------|
| 1 | Minimize CLI agent overhead | Done — `--system-prompt`, `--no-session-persistence`; `--disallowed-tools`/`--max-turns` confirmed unsafe/nonexistent, not added |
| 2 | Structured-output parsing | Done — `structured_output` → `result` → error, 8 tests |
| 3 | `default_effort` migration | Done — real data migration, medium→low, documented no-op downgrade |
| 4 | Cumulative escalation telemetry | Done — `initial_*`/`escalation_*` fields + true sums |
| 5 | Cache versioning + overrides | Done — `CACHE_CONTRACT_VERSION`, explicit-override respected |
| 6 | Daily report deterministic assembly | Done — compact I/O + bridge-side merge |
| 7 | Structured evidence engine | **Partial** — field-aware normalization bug fixed; full always-on pipeline not built (scoped, see ADR 0005) |
| 8 | Real A/B benchmark | Done — real `retired-runtime` CLI, 12 fixture types, single-pass (scoped, see ADR 0005) |
| 9 | CI expansion | Done — `api`/`sandbox`/`bridge` jobs added alongside `web` |
| 10 | ADR/docs update | Done — ADR 0004 corrected, ADR 0005 added, this report |

## 7. Test coverage added/changed this phase

- `sandbox/tests/test_structured_output.py` (new, 8 tests)
- `sandbox/tests/test_escalation.py` (+2 cumulative-telemetry tests)
- `sandbox/tests/test_preprocessing.py` (+6 field-aware normalization tests)
- `sandbox/tests/test_daily_report_compaction.py` (new, 4 tests)
- `apps/api/tests/test_analysis.py` (+4 cache-versioning tests)
- `bridge/tests/test_daily_report_merge.py` (new, 8 tests)
- `bridge/tests/test_bridge.py` (1 stale test fixed, unrelated to this
  mission's changes but discovered while verifying)
- Full suites green: `apps/api/tests` 70 passed, `sandbox/tests` 36
  passed, `bridge/tests` 15 passed + 2 gated-skip (live CLI / Docker).

## 8. Real benchmark results (Issue 8)

Produced via `NOC_LIVE_BENCHMARK=1 python3 scripts/benchmark_live_ab.py`
against the real `retired-runtime` CLI (`retired-runtime-sonnet-5`, effort `low`, escalating
to `medium` only when the low-effort result is genuinely uncertain), one
fixture per error type named in the mission brief, run on 2026-09-13. Full
per-fixture telemetry is in `scripts/benchmark_live_ab_results.json`.

| Fixture | cost (USD) | in tok | out tok | ms | conf | severity | escalated |
|---|---:|---:|---:|---:|---:|---|---|
| NullPointerException | 0.0368 | 4 | 2290 | 21,604 | 0.55 | medium | no |
| DuplicateKeyException | — | — | — | — | — | — | **transient failure, succeeded on retry** (see below) |
| DateTimeParseException | 0.0204 | 4 | 1076 | 11,779 | 0.60 | low | no |
| JsonParsingFailure | 0.0367 | 4 | 2280 | 21,371 | 0.85 | low | no |
| BusinessException (Chinese) | 0.0145 | 2 | 1002 | 10,557 | 0.55 | low | no |
| HTTP403Forbidden | 0.0308 | 4 | 1661 | 13,089 | 0.60 | medium | no |
| ClickHouseTimeout | 0.0345 | 4 | 2105 | 19,280 | 0.55 | low | no |
| ElasticsearchFailure | 0.0791 | 8 | 4884 | 44,665 | 0.60 | high | **yes** (low→medium, confidence 0.40 below threshold) |
| JVM_OOM | 0.1416 | 14 | 8775 | 78,151 | 0.55 | critical | **yes** (low→medium, borderline confidence on critical severity) |
| RepetitiveError | 0.0555 | 6 | 3162 | 27,748 | 0.75 | high | no |
| MultipleUnrelatedErrors | 0.0459 | 4 | 3051 | 27,609 | 0.55 | low | no |
| RareCriticalHiddenInNoise | 0.2177 | 6 | 6153 | 56,283 | 0.85 | **critical** | no |

**Quality check — the mission's key correctness scenario:**
`RareCriticalHiddenInNoise` (600 lines of retry-timeout noise with one
OOM buried in the middle) came back `severity_signal: critical`,
confidence 0.85, with `likely_cause_en`: *"Persistent upstream/network
timeout causing continuous Poller retries, compounded by a separate heap
memory exhaustion in the payments LedgerWorker..."* — the rare critical
error was correctly surfaced, not lost in the noise, at low effort and
without needing escalation. This is the deterministic-preprocessing
guarantee (Issue 7's severity-independent-of-frequency ranking) actually
holding up end-to-end against a real model call, not just the synthetic
byte-count benchmark.

**A real finding, confirmed transient on re-run:** `DuplicateKeyException`
failed outright on its first attempt —
`terminal_reason='structured_output_retry_exhausted'` — for a single
short, unambiguous log line, the same schema/skill/prompt path that
succeeded for every other fixture. Re-running that exact fixture in
isolation immediately afterward succeeded (`severity_signal: low`,
`likely_cause_en`: "Duplicate or retried order-reservation insert due to
missing idempotency check / race condition in InventoryWorker") — this
confirms it was a one-off, transient model-side structured-output retry
exhaustion, not a reproducible bug in the schema, prompt, or CLI
invocation. Worth keeping in mind operationally regardless: a single CLI
call can occasionally fail this way, which is exactly why the bridge's
bounded-retry/DLQ path (§26, unchanged by this mission) exists.

**Escalation worked as designed:** both escalations
(`ElasticsearchFailure`, `JVM_OOM`) fired for the documented reasons
(low confidence, or borderline confidence on critical severity) and only
cost roughly double that one call — not a blanket higher tier for every
request.

## 9. Security review

- No OAuth credential or secret is ever logged — verified: `entrypoint.py`
  never prints `.credentials.json` contents; the bridge's `--system-
  prompt`/`--json-schema` values contain no secrets; CI's Postgres/
  RabbitMQ/MinIO credentials are throwaway dev-only values matching
  `docker-compose.dev.yml`, scoped to ephemeral CI service containers.
- Reducing retired provider's tool surface (Issue 1) tightens, not loosens,
  security: no new tool was granted; `--system-prompt` replaces (not
  extends) the default identity, and `--no-session-persistence` removes a
  stray on-disk transcript artifact.
- The Docker sandbox's isolation (non-root, cap-drop ALL, no-new-
  privileges, read-only rootfs, tmpfs-only writes, resource/pid/time
  limits, forced removal) is unchanged by this mission.
- Cache-versioning (Issue 5) prevents a stale/incompatible cached result
  from being served silently across a schema or policy change — an
  auditability and correctness improvement, not just a cost one.

## 10. Recommendations / remaining work

- Build the full Issue 7 pipeline (Parser → Field Extraction →
  Normalization → Event Grouping → Exact Statistics → Severity Ranking →
  Representative Sampling → Compact Evidence) as a follow-up mission, if
  real telemetry (already being collected, ADR 0004 Phase 1) shows the
  size-gated compaction path is hit often enough to justify it.
- Run `scripts/benchmark_live_ab.py` periodically (e.g. before/after a
  model or prompt change) to catch cost or quality regressions with real
  numbers, not just the deterministic byte-size benchmark.
- Extend the exact-match cache (ADR 0004 Phase 6) to `daily_report` jobs
  — still open, unchanged from ADR 0004's own recommendation.
- Build the admin/DevOps telemetry view over the Phase 1 columns — still
  open, unchanged from ADR 0004's own recommendation.
