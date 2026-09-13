# 0008 — Findings from live end-to-end personal testing

- Status: **implemented**
- Date: 2026-09-13

## Context

After ADR 0007 shipped, the operator ran the full stack locally against
real Postgres/RabbitMQ/MinIO/Docker and a real Claude Code CLI
invocation, rather than only the test suites, to personally verify the
Skill Runtime mission end-to-end. That surfaced four real defects the
test suites had not caught — three because they only occur against
long-lived, real infrastructure state that no test fixture reproduces,
and one because it's an operational footgun in the local dev workflow
itself rather than application code. Each is a genuine bug, not a
misunderstanding of intended behavior; the "budget_exhausted" case
encountered in the same session was investigated and found to be the
opposite — the Claude CLI's own cost-safety mechanism working as
designed — so it is not listed here as a fix.

## Decision

**1. `materialize_snapshot`'s per-job scratch directory blocked the
sandbox's uid** (`212512f`). `tempfile.TemporaryDirectory()` defaults to
mode 0700 (host-user-only); every job with a `skill_hash` — i.e. every
normal job post-mission — materializes its skill snapshot into one of
these before the sandbox mounts it read-only at `/skills`, so every such
job failed with `PermissionError` reading `SKILL.md`. Fixed by widening
exactly the "other" bits needed (0o705 on the two directories, 0o604 on
the three snapshot files), mirroring the same reasoning
`sandbox_runner.py::_grant_sandbox_uid_access` already applied to
`input_dir`/`output_dir` in ADR 0007 step 17 — this one scratch
directory was simply missed at the time.

**2. `delete_incident` violated the `outbox_events.job_id` foreign key**
(`5d69019`). The route's cascading delete was written before ADR 0006
introduced the outbox pattern, and was never updated afterward: it
cleared `Evidence`/`AnalysisRun`/`Job` rows but not the `OutboxEvent`
rows referencing those jobs. Deleting any incident with a real
dispatched job (i.e. any incident that had gone through analysis) hit an
unhandled `ForeignKeyViolation`, which FastAPI turned into a 500 the
frontend reported as an opaque "Failed to fetch". Fixed by deleting the
incident's jobs' `OutboxEvent` rows first.

**3. `ensure_image_built` never rebuilt an existing sandbox image**
(`0a98fdb`). It only checked whether `noc-sandbox:spike` existed at all,
so once built — 2026-09-10, in this case — it was assumed current
forever regardless of source changes. Every job in this session silently
ran a stale `entrypoint.py`, three days and several commits behind,
including missing the Phase 1 AI-usage-telemetry feature entirely (this
is why `telemetry.json`/per-run token and cost figures were never
produced for any job run this session, despite the feature's code being
correct and already covered by its own tests). Fixed by hashing the
build context (`Dockerfile` + `entrypoint.py`) and stamping the hash as
an image label, rebuilding whenever it no longer matches.

**4. `apps/api`'s test suite runs against the operator's live dev
database, with no isolation.** `tests/conftest.py` builds its engine
from `settings.database_url` directly — the same Postgres the running
API and bridge use for real data — and its fixtures drop all tables per
run. Running the full suite mid-session (to verify fix #2) wiped every
incident, job, and analysis run the operator had created while testing
live, down to an empty schema. Recovered via `alembic upgrade head` +
re-seed, but the actual test data itself was unrecoverable. **Not yet
fixed** — flagged here as a known gap; the fix under discussion is
pointing the test engine at a separate database (e.g. a
`TEST_DATABASE_URL` override) so a routine test run can never touch
development data.

## Consequences

- Fixes #1-#3 are shipped, tested (each has a dedicated regression test:
  `test_materialize_snapshot_grants_sandbox_uid_read_access`,
  `test_admin_can_delete_incident_whose_job_has_an_outbox_event`,
  `test_ensure_image_built_rebuilds_when_content_hash_differs` and
  siblings), and pushed to `reliability-mission-batches-b-d`.
- #3's fix means a sandbox rebuild now happens automatically on the next
  job after any `sandbox/` source change, at the cost of one image-build
  (seconds) on that job's critical path instead of none — an accepted
  trade for never again silently running stale code.
- #4 remains an open risk: until it's fixed, running `pytest` for
  `apps/api` anywhere against this same Postgres instance — including a
  future session, a CI misconfiguration pointed at the wrong host, or a
  teammate's shared dev environment — will silently destroy its data
  again with no warning.
