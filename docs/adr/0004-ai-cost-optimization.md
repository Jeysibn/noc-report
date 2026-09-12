# 0004 — AI Usage & Cost Optimization (in progress)

Status: **in progress**, tracked across multiple work sessions. This ADR is
the running log for the cost-optimization mission (see mission brief, not
committed to the repo) so work can resume without re-deriving the plan.

## Phase checklist

- [x] Phase 0 — inspect current pipeline (see "Pipeline map" below)
- [x] Phase 5 (partial) — fix rare-severe-event-preservation bug in
      `sandbox/entrypoint.py`'s log compaction (frequency-only ranking was
      dropping rare critical errors like a single OOM buried in 80k WARNs)
- [x] Phase 1 — AI usage telemetry persisted to Postgres (previously only
      printed to stderr from `sandbox/entrypoint.py`'s `run_skill`)
- [x] Phase 2/3 — trim Claude Code agent overhead / minimal system prompt.
      Re-inspected `_invoke_claude` (sandbox/entrypoint.py): no tools are
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
- [ ] Phase 5 (remainder) — always-on structured preprocessing (currently
      compaction only engages once `MAX_LOG_CHARS` is exceeded; below that,
      raw text is sent verbatim with no statistics/severity extraction)
- [x] Phase 6 — exact-match result cache: `POST .../analysis-runs` looks up
      a prior *completed* `AnalysisRun` with the same `Evidence.sha256` +
      `skill_name` + `skill_version` before ever enqueuing a job; on a hit
      it creates the `Job` already `COMPLETED` (`used_cache=true,
      cache_type="exact"`) and copies the cached `result_json` into a new
      `AnalysisRun` — zero RabbitMQ message, zero bridge/sandbox/Claude
      invocation
- [ ] Phase 7 — pattern/signature cache for recurring incident types
- [x] Phase 8 — daily report already reuses existing per-incident analyses
      unmodified (`skills/daily-alert-report/SKILL.md`); no work needed
- [ ] Phase 9 — output size control (schema already concise; no changes made)
- [ ] Phase 10 — centralize AI policy config (partially done via
      `BridgeSettings`; effort/model/budget already centralized there)
- [x] Phase 11 — `--max-budget-usd` already used only as a guardrail, not
      the primary lever (no change needed)
- [ ] Phase 12 — benchmark dataset + before/after report
- [ ] Phase 13 — broader test coverage (started: `sandbox/tests/`)

## Pipeline map (as inspected)

```
FastAPI (apps/api) -> jobs table (Postgres) -> RabbitMQ
  -> bridge/noc_bridge/service.py (consumer)
    -> bridge/noc_bridge/sandbox_runner.py (Docker SDK, non-root, cap-drop
       ALL, no-new-privileges, read-only rootfs, tmpfs /tmp, network
       disabled except for the Claude-invoking job type, CPU/mem/pid
       limits, force-remove)
      -> sandbox/entrypoint.py (in-container)
        -> compacts log.txt if > SKILL_MAX_LOG_CHARS (80,000 by default)
        -> builds prompt = SKILL.md + input text (no extra system prompt,
           no tools, no MCP, no subagents)
        -> shells out to `claude -p --output-format json --model ...
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
  evidence sent to Claude, even if it wouldn't make the top
  `MAX_PATTERN_GROUPS` by frequency alone. Previously a single rare
  critical error buried under high-volume noise (e.g. 80,000 WARN retries)
  could be silently dropped before ever reaching Claude — a correctness
  bug, not just a cost one, since it could cause a missed root cause.
- `sandbox/tests/test_preprocessing.py`: regression tests, including the
  exact "one OOM among 80k WARNs" scenario from the mission brief.
- `sandbox/entrypoint.py`: extracted `_invoke_claude` (single CLI call) out
  of `run_skill`, added `_escalation_reason`/`_EFFORT_RANK`, default
  `SKILL_EFFORT` fallback changed `medium` -> `low`. `run_skill` now makes
  one low-effort call and, only for log-triage-summary and only when
  `_escalation_reason` finds a real problem, one bounded escalated retry.
- `apps/api/app/models/models.py` (`SystemConfig.default_effort`) and
  `bridge/noc_bridge/db.py` (`_SYSTEM_CONFIG_DEFAULTS`): default value
  `medium` -> `low` (no migration needed — no DB-level `server_default`
  existed, this only changes the value used when seeding a fresh row).
- `bridge/noc_bridge/config.py`: added `claude_effort_escalation` (default
  `medium`) and `claude_escalation_confidence_threshold` (default `0.55`)
  to `BridgeSettings`, centralizing the escalation policy alongside the
  existing model/budget/compaction settings.
- `bridge/noc_bridge/service.py`: passes `SKILL_EFFORT_ESCALATION` /
  `SKILL_ESCALATION_CONFIDENCE_THRESHOLD` through to the sandbox
  environment.
- `apps/api/tests/test_admin.py`: updated seeded-default assertion
  (`default_effort == "medium"` -> `"low"`).
- `sandbox/tests/test_escalation.py`: unit tests for `_escalation_reason`/
  `_EFFORT_RANK`, plus two `run_skill`-level tests (via a monkeypatched
  `_invoke_claude`) proving escalation fires exactly once for an uncertain
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
  `cache_creation_tokens`/`cache_read_tokens` from the Claude CLI's JSON
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
