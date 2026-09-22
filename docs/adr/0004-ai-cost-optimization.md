# 0004 — AI Usage & Cost Optimization (in progress)

Status: **in progress**, tracked across multiple work sessions. This ADR is
the running log for the cost-optimization mission (see mission brief, not
committed to the repo) so work can resume without re-deriving the plan.

> **Correction (Phase 2 of this mission — see ADR 0005):** two claims below
> did not hold up under a fresh mission (with its own explicit "inspect the
> real current source, don't trust old docs" instruction) and are corrected
> here rather than silently left standing:
> - **"Phase 2/3 ... verified as already minimal, no code change made"**
>   was wrong on the *session-persistence* and *system-prompt* dimensions
>   specifically: the CLI invocation had no `--system-prompt` (so it used
>   the CLI's own default coding-agent identity/preamble, real overhead
>   this skill's structured single-turn output never needed) and no
>   `--no-session-persistence` (so a transcript was written and kept for
>   an analysis that is never resumed). Both are real, safe flags this
>   CLI build supports and are now set — see ADR 0005 Issue 1.
> - **"Phase 8 — daily report already reuses existing per-incident
>   analyses unmodified ... no work needed"** was true only in the narrow
>   sense that stale per-incident data wasn't being regenerated. It missed
>   that the *daily-report skill's own schema* still round-tripped every
>   incident's full analysis object, screenshots, and MinIO/Grafana
>   references through the prompt/response even though none of that is
>   reasoning-dependent — real, avoidable token cost. Fixed in ADR 0005
>   Issue 6 (compact input, compact output, deterministic merge).
>
> Everything else below (Phases 0, 1, 4, 5, 6, 7, 9-13, and the benchmark
> numbers) was re-checked against the current `main` and still holds.

> **Phase 5 runtime note:** the historical cache and skill-version wording
> below predates authoritative `SkillSnapshot` execution. Current jobs use
> `skill_snapshot_id`/content hash as the immutable identity; the old
> `skill_name` + `skill_version` descriptions are retained only as historical
> implementation notes. See ADR 0011 for the current contract.

## Phase checklist

- [x] Phase 0 — inspect current pipeline (see "Pipeline map" below)
- [x] Phase 5 (partial) — fix rare-severe-event-preservation bug in
      `sandbox/entrypoint.py`'s log compaction (frequency-only ranking was
      dropping rare critical errors like a single OOM buried in 80k WARNs)
- [x] Phase 1 — AI usage telemetry persisted to Postgres (previously only
      printed to stderr from `sandbox/entrypoint.py`'s `run_skill`)
- [x] Phase 2/3 — trim retired provider runtime agent overhead / minimal system prompt.
      Re-inspected `_invoke_retired-runtime` (sandbox/entrypoint.py): no tools are
      requested, `--restricted` + `--permission-mode dontAsk` +
      `--permission-prompts none` already strip any agentic tool-use
      overhead, and the prompt sent is just the skill's own SKILL.md
      content — not a generic coding-agent system prompt. Nothing left to
      trim without dropping below what the skill schema itself needs; no
      code change made, verified as already minimal.
- [x] Phase 4 — default effort LOW + escalation path (`system_config.
      default_effort` and the bridge's fallback both now default to
      `low`; `sandbox/entrypoint.py` escalates once to `SKILL_EFFORT_
      ESCALATION` (default `medium`) when the low-effort result has
      invalid structure, empty `key_finds`, confidence below
      `SKILL_ESCALATION_CONFIDENCE_THRESHOLD` (default 0.55), or
      `severity_signal: critical` with confidence < 0.75 — single bounded
      retry, no loop; only applies to log-triage-summary, which is the
      only skill with a per-run `confidence`)
- [x] Phase 5 (remainder) — evaluated always-on structured preprocessing
      below `MAX_LOG_CHARS`; **deliberately not implemented** — see
      "Remaining opportunities: not worth implementing" below for why
- [x] Phase 6 — exact-match result cache: `POST .../analysis-runs` looks up
      a prior *completed* `AnalysisRun` with the same `Evidence.sha256` +
      `skill_name` + `skill_version` before ever enqueuing a job; on a hit
      it creates the `Job` already `COMPLETED` (`used_cache=true,
      cache_type="exact"`) and copies the cached `result_json` into a new
      `AnalysisRun` — zero RabbitMQ message, zero bridge/sandbox/retired provider
      invocation
- [x] Phase 7 — evaluated a near-duplicate pattern/signature cache;
      **deliberately deferred** as optional future work, see below (Phase 6's
      exact-match cache already covers byte-identical re-analysis, which is
      the safe/high-confidence subset of this idea)
- [x] Phase 8 — daily report already reuses existing per-incident analyses
      unmodified (`skills/daily-alert-report/SKILL.md`); no work needed
- [x] Phase 9 — output size control: schema reviewed, already concise
      (five short fields for log-triage-summary); no changes made
- [x] Phase 10 — AI policy config centralized in `BridgeSettings`
      (`bridge/noc_bridge/config.py`): model/effort/budget/timeout/
      escalation policy/threshold all live there, sourced from env vars
      with one place to change defaults
- [x] Phase 11 — `--max-budget-usd` already used only as a guardrail, not
      the primary lever (no change needed)
- [x] Phase 12 — benchmark: `scripts/benchmark_preprocessing.py`, see
      "Benchmark" section below for real measured numbers
- [x] Phase 13 — test coverage: `sandbox/tests/` (17 tests across
      preprocessing/escalation/telemetry), `apps/api/tests/` (66 tests,
      incl. cache + telemetry sync), `bridge/tests/test_bridge.py` gained a
      telemetry.json assertion in the real end-to-end test

## Pipeline map (as inspected)

```
FastAPI (apps/api) -> jobs table (Postgres) -> RabbitMQ
  -> bridge/noc_bridge/service.py (consumer)
    -> bridge/noc_bridge/sandbox_runner.py (Docker SDK, non-root, cap-drop
       ALL, no-new-privileges, read-only rootfs, tmpfs /tmp, network
       disabled except for the retired provider-invoking job type, CPU/mem/pid
       limits, force-remove)
      -> sandbox/entrypoint.py (in-container)
        -> compacts log.txt if > SKILL_MAX_LOG_CHARS (80,000 by default)
        -> builds prompt = SKILL.md + input text (no extra system prompt,
           no tools, no MCP, no subagents)
        -> shells out to `retired-runtime -p --output-format json --model ...
           --effort ... --permission-mode dontAsk --permission-prompts none
           --restricted --max-budget-usd ... --json-schema ...`, prompt via
           stdin (not argv, to avoid E2BIG on large logs)
        -> parses result.json, writes to /output, prints cost/duration/
           turns/usage to stderr (not yet persisted to Postgres)
      -> bridge uploads result.json to MinIO + updates jobs/analysis_runs
         rows in Postgres
```

Two skills exist: `log-triage-summary` (single log -> bilingual triage) and
`daily-alert-report` (shift snapshot, including already-computed per-
incident analyses, -> bilingual report; does NOT re-run analysis per
incident, so Phase 8 was effectively already done before this mission).

## Changes made so far

- `sandbox/entrypoint.py`: `_compact_log_if_oversized` now treats severity
  and frequency as two independent selection axes. Any pattern matching
  `_SEVERITY_MARKERS` (FATAL, OOM, StackOverflowError, SecurityException,
  data loss, corruption, deadlock, panic) is always included in the
  evidence sent to retired provider, even if it wouldn't make the top
  `MAX_PATTERN_GROUPS` by frequency alone. Previously a single rare
  critical error buried under high-volume noise (e.g. 80,000 WARN retries)
  could be silently dropped before ever reaching retired provider — a correctness
  bug, not just a cost one, since it could cause a missed root cause.
- `sandbox/tests/test_preprocessing.py`: regression tests, including the
  exact "one OOM among 80k WARNs" scenario from the mission brief.
- `sandbox/entrypoint.py`: extracted `_invoke_retired-runtime` (single CLI call) out
  of `run_skill`, added `_escalation_reason`/`_EFFORT_RANK`, default
  `SKILL_EFFORT` fallback changed `medium` -> `low`. `run_skill` now makes
  one low-effort call and, only for log-triage-summary and only when
  `_escalation_reason` finds a real problem, one bounded escalated retry.
- `apps/api/app/models/models.py` (`SystemConfig.default_effort`) and
  `bridge/noc_bridge/db.py` (`_SYSTEM_CONFIG_DEFAULTS`): default value
  `medium` -> `low` (no migration needed — no DB-level `server_default`
  existed, this only changes the value used when seeding a fresh row).
- `bridge/noc_bridge/config.py`: added `retired-runtime_effort_escalation` (default
  `medium`) and `retired-runtime_escalation_confidence_threshold` (default `0.55`)
  to `BridgeSettings`, centralizing the escalation policy alongside the
  existing model/budget/compaction settings.
- `bridge/noc_bridge/service.py`: passes `SKILL_EFFORT_ESCALATION` /
  `SKILL_ESCALATION_CONFIDENCE_THRESHOLD` through to the sandbox
  environment.
- `apps/api/tests/test_admin.py`: updated seeded-default assertion
  (`default_effort == "medium"` -> `"low"`).
- `sandbox/tests/test_escalation.py`: unit tests for `_escalation_reason`/
  `_EFFORT_RANK`, plus two `run_skill`-level tests (via a monkeypatched
  `_invoke_retired-runtime`) proving escalation fires exactly once for an uncertain
  low-effort result and not at all for a confident one.
- Verified: `apps/api/tests` (64 passed) and `sandbox/tests` (14 passed)
  still green after these changes.

### Phase 6 — exact-match result cache

- `apps/api/alembic/versions/a1b2c3d4e5f6_...py`: new migration adding
  `jobs.used_cache`/`jobs.cache_type` and `analysis_runs.used_cache`/
  `analysis_runs.cache_type` (all nullable/defaulted, no backfill needed).
- `apps/api/app/models/models.py`: `Job`/`AnalysisRun` gain those columns.
- `apps/api/app/schemas/schemas.py`: `AnalysisRunOut` surfaces
  `used_cache`/`cache_type`.
- `apps/api/app/api/v1/routers/analysis.py`: added `CACHE_POLICY_VERSION`
  comment (folded into `SKILL_VERSION` for now — see the code comment for
  why a single combined version was chosen over separately tracking
  schema/preprocessor/model-policy versions) and
  `_find_cached_analysis_run` (exact match on `Evidence.sha256` +
  `skill_name` + `skill_version`, requiring `result_json IS NOT NULL`).
  `request_analysis` checks this before enqueuing; on a hit, builds the
  `Job`/`AnalysisRun` pair directly as already-`COMPLETED`/cache-tagged
  instead of publishing to RabbitMQ.
- `apps/api/tests/test_analysis.py`: new
  `test_request_analysis_exact_cache_hit_skips_queue` — completes one
  incident's analysis, then requests analysis on a second incident with
  byte-identical log evidence and asserts it comes back `COMPLETED`
  immediately with `used_cache: true`/`cache_type: "exact"` and that no
  message was published to the `log_triage` queue. Also added an explicit
  `sha256` to the shared `_upload_log_evidence` test helper (previously
  omitted, which defeated the cache key).
- Verified: `apps/api/tests` full suite (65 passed, sqlite-backed test DB
  which uses `Base.metadata.create_all`, so the new columns applied
  without needing the Alembic migration run for tests to pass). The
  Alembic migration itself was smoke-checked against the shared dev
  Postgres and is syntactically valid, but that DB's `jobs` table is
  currently missing independent of this change (pre-existing
  inconsistency, not introduced here) — worth a follow-up to reconcile
  that dev DB's migration state separately from this mission.
- The lookup is global over `AnalysisRun` (not incident-scoped), so it
  also dedupes across *different* incidents that happen to have
  byte-identical log evidence attached — covered by the test above.
  Not yet done: an equivalent cache for `daily_report` jobs (lower
  priority — Phase 8 already avoids regenerating per-incident analysis
  there) and the pattern cache (Phase 7) for near-duplicate-but-not-
  identical inputs.

### Phase 1 — AI usage telemetry

- `sandbox/entrypoint.py`: added `_envelope_telemetry(envelope)` extracting
  `cost_usd`/`duration_ms`/`num_turns`/`input_tokens`/`output_tokens`/
  `cache_creation_tokens`/`cache_read_tokens` from the retired provider CLI's JSON
  envelope's `usage` block. `run_skill` now returns `tuple[dict, dict]`
  (result, telemetry) instead of a bare `dict`: telemetry also carries
  `model`, `effort` (updated to the escalated value if escalation fired),
  `raw_input_bytes`/`evidence_bytes`/`preprocessing_ratio` (pre- vs
  post-compaction byte counts, from Phase 5's log compaction),
  `escalated`/`escalation_reason` (from Phase 4's escalation logic), and
  `confidence` (copied from the final result). `main()` writes this to a
  new `telemetry.json` alongside the existing `result.json` — kept as a
  separate file rather than folded into `result.json` because the skill
  output schemas declare `additionalProperties: false`, so adding fields
  there would break schema validation.
- `bridge/noc_bridge/service.py`: best-effort upload of `telemetry.json` to
  `noc-job-artifacts` (`jobs/{job_id}/telemetry.json`) alongside the
  existing result/report upload; a failure here only logs a warning, it
  never fails the job (telemetry is diagnostic, not part of the job's
  contract).
- `apps/api/alembic/versions/b2c3d4e5f6a7_ai_usage_telemetry.py`: new
  migration adding 13 nullable telemetry columns to `analysis_runs`
  (`input_tokens`, `output_tokens`, `cache_creation_tokens`,
  `cache_read_tokens`, `estimated_cost_usd`, `duration_ms`, `num_turns`,
  `confidence`, `escalated`, `escalation_reason`, `raw_input_bytes`,
  `evidence_bytes`, `preprocessing_ratio`) with a matching `downgrade()`.
- `apps/api/app/models/models.py`: `AnalysisRun` gains those same 13
  columns.
- `apps/api/app/api/v1/routers/analysis.py`: `_sync_completed_job` now
  also best-effort-fetches `jobs/{job.id}/telemetry.json` right after
  pulling `result.json`, and merges whichever fields are present onto the
  `AnalysisRun` row (missing file, malformed JSON, or an older run with no
  telemetry all degrade silently to "leave the columns null" — never
  raises, never blocks the poll from returning the result). `_to_out` now
  surfaces all 13 fields on `AnalysisRunOut`.
- `apps/api/app/schemas/schemas.py`: `AnalysisRunOut` gains the 13
  telemetry fields, all defaulting to `None`/`False` so older runs and
  cache hits (which have no `telemetry.json`) serialize cleanly.
- `apps/api/tests/test_analysis.py`: new
  `test_poll_syncs_telemetry_when_available` — uploads `result.json` +
  `telemetry.json` for one job and asserts every telemetry field round-
  trips through `AnalysisRunOut`; uploads only `result.json` (no
  telemetry) for a second job and asserts the poll still succeeds with
  every telemetry field `None`/`False`, proving the best-effort contract.
- Verified: `apps/api/tests` full suite (65 passed, then 66 with the new
  test) and `sandbox/tests` (17 passed, including the 3 new
  `test_telemetry.py` cases) both green.
- Not yet done: a UI surface for this data (mission mentions an
  admin/DevOps diagnostic view — avg cost/tokens/duration, cache hit rate,
  escalation rate, preprocessing ratio) — deferred, this phase only
  covers making the numbers exist and reach Postgres reliably.

## Benchmark

`scripts/benchmark_preprocessing.py` measures the one thing reliably
measurable without spending real retired provider subscription usage just to
produce a number: Phase 5's deterministic log-compaction effect on input
size, across synthetic logs shaped like real incident logs (repeating
WARN-retry noise plus one rare FATAL/OOM). Actual run, 2026-09-12:

```
scenario                                    raw bytes   evidence bytes  reduction  severe kept
small (500 lines, under threshold)             32,735           32,735      0.0%         True
medium (5,000 lines)                          334,205              904     99.7%         True
large (60,000 lines, one buried OOM)        4,134,970              908    100.0%         True
```

Reading this: below `MAX_LOG_CHARS` (80,000 chars) nothing is compacted —
by design, see "not worth implementing" below. Once a log crosses that
threshold, compaction collapses repeated-pattern noise to ~1-2
representative lines + an exact count per pattern, typically a >99%
reduction in bytes actually sent to retired provider, while the rare severe event
(the OOM) is always still present in the output (`severe kept: True` in
every scenario) — this is the Phase 5 bug-fix regression-tested in
`sandbox/tests/test_preprocessing.py`.

Fewer input bytes at a fixed effort tier directly lowers `input_tokens`
(now visible per-run via Phase 1's telemetry). Combined with the Phase 4
default-to-low-effort-with-escalation policy (most runs never need the
medium-effort retry — only `_escalation_reason` failures do) and the
Phase 6 exact-match cache (repeat analysis of byte-identical evidence
costs literally zero retired provider invocations), the three phases compound: a
large, recurring, mostly-noise incident log now (a) frequently costs
nothing at all on a re-analysis, and (b) even on a fresh analysis, sends
under 1% of its raw bytes at the cheapest effort tier by default.

Live, per-run cost/token numbers (not synthetic) are now captured
automatically by Phase 1 for every real job going forward — once this has
been running against real incidents for a while, `estimated_cost_usd`/
`input_tokens`/`escalated` on `analysis_runs` give an actual before/after
comparison against pre-mission jobs, without needing a separate live
benchmark run that would itself cost real subscription usage.

## Quality comparison

- Phase 5's bug fix is a strict quality *improvement*, not a tradeoff — it
  fixes a case where a critical error could previously be silently
  dropped from what retired provider sees.
- Phase 4's escalation path is designed so effort tier is a cost lever,
  not a quality lever: low effort is attempted first, but any output
  that's structurally invalid, has no key finds, or reports low/borderline
  confidence on a critical-severity read is escalated to medium
  automatically — the accuracy floor is protected by the escalation
  condition itself, not by picking a uniformly higher tier for everyone.
- Phase 6's exact cache only reuses a result for byte-identical input
  under the same skill/schema version — it cannot serve a stale result
  for a log that has actually changed, and bumping `SKILL_VERSION`
  invalidates it wholesale if the schema/preprocessing/policy changes.
- No change in this mission alters the bilingual output, independent
  per-log analysis, or the structured JSON schema retired provider must satisfy —
  quality-relevant behavior is unchanged except where explicitly improved
  (Phase 5).

## Remaining opportunities

**Recommended next:**
- Build a small admin/DevOps view over the new telemetry columns (avg
  cost/tokens/duration, cache hit rate, escalation rate, average
  preprocessing ratio) — the data now exists in Postgres (Phase 1); this
  is purely a read-only reporting UI on top of it, no pipeline change.
- Extend the exact-match cache (Phase 6) to `daily_report` jobs, keyed on
  the same set of underlying incident analyses — currently only
  `log_triage` jobs are cached.

**Optional future:**
- Phase 7, a near-duplicate pattern/signature cache (reusing e.g. the
  `likely_cause` field for a log that's structurally similar but not
  byte-identical to a prior one, while still recomputing counts/
  timestamps/etc from the real input). Deferred rather than built now
  because doing it safely needs a similarity threshold and a way to
  validate that the reused semantic fields still apply — getting that
  wrong silently degrades analysis quality, which this mission's
  constraints explicitly forbid trading away for cost. Worth revisiting
  once real telemetry (Phase 1) shows how often near-duplicate-but-not-
  identical logs actually recur in practice; if that rate is low, the
  complexity isn't worth it.

**Not worth implementing:**
- Phase 5 "always-on" structured preprocessing for logs *under*
  `MAX_LOG_CHARS`. A small log is already cheap at the token level, and
  running it through the same signature-collapse/frequency-ranking logic
  used for oversized logs would only risk losing verbatim detail (exact
  wording, ordering, adjacent context) for no measurable cost benefit —
  the benchmark above shows 0% reduction is expected and correct at that
  size; there's nothing to compact. Compaction is deliberately a "kicks
  in only when it has to" mechanism, not a default transform.
- Trimming the retired provider runtime CLI invocation further (Phase 2/3 remainder) —
  already verified minimal (see Phase 2/3 above); there is no more
  overhead to remove without dropping something the skill schema needs.
- Moving off subscription/OAuth billing to `RETIRED_PROVIDER_API_KEY` — out of
  scope per the mission's own constraints, and not recommended even as an
  option: the existing architecture (bridge + sandboxed CLI + OAuth
  credentials bind-mount) is the one being optimized, not replaced.

## Notes for continuing this work

- No pytest.ini exists yet under `sandbox/` — tests were run directly with
  `python3 -m pytest sandbox/tests/` from the repo root during this pass.
- `bridge/noc_bridge/config.py` already centralizes model/effort/budget/
  compaction constants (`BridgeSettings`) — extend this rather than adding
  new scattered constants for exact/pattern cache config, escalation
  thresholds, etc.
- `apps/api/app/models/models.py` has `analysis_runs` (Milestone 13) — this
  is the natural home for new telemetry columns (Phase 1), following the
  existing migration convention in `apps/api/alembic/`.
- Exact-cache (Phase 6) should key on `SHA256(canonicalized raw input)`
  plus a compatibility tuple (schema version, skill version, model-policy
  version, preprocessor version) — none of these versions are currently
  tracked anywhere; they'll need to be introduced as part of that phase.
