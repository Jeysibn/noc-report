# 0004 — AI Usage & Cost Optimization (in progress)

Status: **in progress**, tracked across multiple work sessions. This ADR is
the running log for the cost-optimization mission (see mission brief, not
committed to the repo) so work can resume without re-deriving the plan.

## Phase checklist

- [x] Phase 0 — inspect current pipeline (see "Pipeline map" below)
- [x] Phase 5 (partial) — fix rare-severe-event-preservation bug in
      `sandbox/entrypoint.py`'s log compaction (frequency-only ranking was
      dropping rare critical errors like a single OOM buried in 80k WARNs)
- [ ] Phase 1 — AI usage telemetry persisted to Postgres (currently only
      printed to stderr from `sandbox/entrypoint.py`'s `run_skill`)
- [ ] Phase 2/3 — trim Claude Code agent overhead further / minimal system
      prompt (current invocation is already close: no tools requested,
      `--restricted`, `--permission-mode dontAsk`, `--permission-prompts
      none`, system prompt is just the skill's own SKILL.md, not a generic
      coding-agent prompt)
- [ ] Phase 4 — default effort LOW + escalation path (currently defaults to
      `medium` via `system_config.default_effort` / `SKILL_EFFORT`)
- [ ] Phase 5 (remainder) — always-on structured preprocessing (currently
      compaction only engages once `MAX_LOG_CHARS` is exceeded; below that,
      raw text is sent verbatim with no statistics/severity extraction)
- [ ] Phase 6 — exact-match result cache (SHA256 of canonicalized input)
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
