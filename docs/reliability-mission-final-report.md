# Reliability Mission — Final Report

Full mission scope covered: Batch A (transactional outbox + job lease,
implemented before this report's session), Batch B (Skill Registry),
Batch C (failure classification gaps), Batch D (Skill Admin activation +
credential hardening). See `docs/adr/0006-reliability-mission.md` for the
architectural decision record; this report is the mission's required
structured summary.

## Current State (before this mission)

- `enqueue_job` published to RabbitMQ synchronously inside the same
  request that wrote the `Job` row — a crash between the DB commit and the
  publish call silently dropped the job with no record it should ever have
  been sent.
- A skill's identity was two free-text fields, `skill_name`/
  `skill_version` — nothing tied a "version" label to the actual prompt/
  schema content behind it, so a `SKILL.md` edit with a forgotten version
  bump silently poisoned the cache and the audit trail.
- `bridge/noc_bridge/service.py` retried every job failure identically (a
  bare `except` up to `MAX_ATTEMPTS`) — a permanently-invalid schema or an
  unsupported job type burned the full retry budget reproducing the exact
  same failure, at real retired provider-CLI cost for a retired provider-invoking job.
- `bridge/noc_bridge/failures.py` already referenced a
  `structured_output_retry_exhausted` retryable-failure marker that
  nothing in `sandbox/entrypoint.py` actually produced — dead code
  masquerading as a real safety net.
- Every credential in `apps/api/app/core/config.py` shipped a convenient,
  publicly-known local-dev default with no check stopping that same
  default from reaching a real deployment.

## Problems Confirmed

All four Phase 0 findings above were reproduced/verified directly (not
assumed) before being fixed — the `structured_output_retry_exhausted`
gap specifically was confirmed by grepping `sandbox/entrypoint.py` for the
literal string and finding zero matches.

## Architecture Changes

- Two-phase enqueue-then-dispatch: `enqueue_job` writes `Job` +
  `OutboxEvent` atomically; a background dispatcher thread
  (`app/outbox.py`/`app/outbox_worker.py`) publishes pending events to
  RabbitMQ afterward and retries on failure.
- Skill content hashing: `apps/api/app/skills/registry.py` (API) and
  `bridge/noc_bridge/skill_registry.py` (bridge, hand-duplicated per this
  repo's separate-deployable convention) make a skill's sha256 content
  hash its real identity, recorded as immutable `SkillSnapshot` rows.
- Bounded structured-output retry in `sandbox/entrypoint.py::_invoke_retired-runtime`
  (retry once, then raise `structured_output_retry_exhausted`), completing
  the retry contract `bridge/noc_bridge/failures.py` already expected.
- Failure classification (`bridge/noc_bridge/failures.py::classify_failure`)
  replacing the bare `except` with RETRYABLE/TERMINAL sorting.
- Startup credential-safety gate
  (`assert_production_secrets_are_safe`, called from `main.py`'s lifespan).

## Files Changed

See `git diff --stat` / `git status` for the exact list; the significant
new modules are `apps/api/app/skills/registry.py`,
`bridge/noc_bridge/skill_registry.py`, `bridge/noc_bridge/failures.py`,
`apps/api/app/outbox.py`, `apps/api/app/outbox_worker.py`, plus the
`skills/*/output.schema.json` and `skills/*/skill.yaml` manifest files and
the new admin endpoints in `apps/api/app/api/v1/routers/admin.py`.

## Database Changes

Two new Alembic migrations: `e5f6a7b8c9d0` (transactional outbox + job
lease columns) and `f0d9050eef0f` (`skill_snapshots` table plus
`skill_hash` columns on `jobs`/`analysis_runs`/`reports`).

## RabbitMQ Changes

None to the topology itself — confirmed (Batch C) that
`bridge/noc_bridge/queue_topology.py` and `apps/api/app/core/queue.py`
already agree, and added `bridge/tests/test_queue_topology_drift.py` so
that stays true going forward.

## Skill System

Content-hash-based `SkillSnapshot`s, `skill_hash` threaded through every
job/analysis-run/report row and the RabbitMQ payload, bridge-side
pre-execution drift verification, and (Batch D) an admin activation
workflow (`GET /admin/skills`, `GET /admin/skills/{name}/versions`,
`POST /admin/skills/{name}/versions/{version_label}/activate`) gated
behind a new `skill.manage` permission and fully audit-logged. Activation
is explicitly bookkeeping/audit visibility, not an execution override —
see ADR 0006's "Decision" section for why, and its "Consequences" section
for the rollback limitation this implies.

## Analysis Workflow / Report Workflow

Unchanged in shape; both now resolve a `skill_snapshot` via
`get_or_create_snapshot` before enqueueing and stamp its `content_hash`
onto the `Job` and onto the `AnalysisRun`/`Report` row. Cache lookups
(`_find_cached_analysis_run`) now require an exact `skill_hash` match, so
a cache hit can never reuse a result produced under different skill
content even if `skill_name`/effort/model all match.

## Failure Recovery

`classify_failure` gives `_process_job` one place to decide
RETRYABLE vs TERMINAL: `UnsupportedJobType`, `OutputValidationError`, and
`SkillHashMismatch` are terminal; `ChecksumMismatch` and an
otherwise-unclassified `RuntimeError` (including a
`structured_output_retry_exhausted` job-level failure) are retryable up to
`MAX_ATTEMPTS`. The sandbox itself now absorbs one bad structured-output
parse per CLI invocation before that failure ever reaches the bridge.

## Benchmark

`scripts/benchmark_reliability.py` (no live retired provider CLI calls, safe to run
anytime) — see `scripts/benchmark_reliability_results.json` for the last
run's output:
- **Job durability:** the old synchronous-publish design's loss window
  was the publish call's own duration (measured ~2e-5s in-process, but
  unbounded in a real broker outage); the new design's window is zero —
  a crash at any point after the DB commit leaves a durable, replayable
  `OutboxEvent` row.
- **Failure classification:** across 3 representative terminal-failure
  scenarios (unsupported job type, invalid output schema, skill-hash
  drift) at `MAX_ATTEMPTS=5`, the old bare-retry approach would have
  wasted 12 total reproduced-failure attempts; classification now avoids
  all 12.
- **Skill drift detection:** a changed `SKILL.md` is detected in ~1e-4s,
  before any retired provider CLI invocation — avoiding the cost of running a job
  against the wrong content, not just the correctness risk.

## Security

New `skill.manage` permission (Admin-only) for the skill activation
endpoints, fully audit-logged via the existing `record_audit`. New startup
gate refusing to boot with `environment=production` while `jwt_secret`,
`minio_secret_key`, or `rabbitmq_url` are still at their known local-dev
default values (never enforced in development).

## Remaining Risks

- `set_active_snapshot` cannot make the bridge execute an older
  snapshot's actual content when on-disk files have moved on — a true
  rollback still requires reverting the files themselves. Documented
  explicitly in ADR 0006 and in the function's own docstring so this
  isn't mistaken for a real rollback mechanism later.
- No login rate-limiting/brute-force protection — `auth.py`'s
  failed-login audit trail is visibility only, not prevention.
- The production-secrets check covers the three most likely
  copy-pasted-from-this-repo credentials; it is not an exhaustive secrets
  audit (e.g. it does not inspect `.env` file permissions or catch a
  weak-but-non-default secret).
- No docker-compose file exists in this repo to actually exercise the
  `environment=production` gate end-to-end outside of tests; it is
  unit-tested directly against `Settings`, not via a full container boot.
