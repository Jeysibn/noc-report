# 0006 — Reliability Mission (Batches A-D)

- Status: **implemented**
- Date: 2026-09-13

## Context

Following the AI cost-optimization mission (ADR 0004/0005), a separate
mission targeted the platform's *reliability* rather than its AI spend:
job delivery guarantees, skill/prompt versioning integrity, failure
classification, and credential/permission hardening. Phase 0 inspection
found four concrete gaps:

1. `enqueue_job` published directly to RabbitMQ inside the same request
   that wrote the `Job` row — a crash or RabbitMQ outage between the two
   silently dropped a job the caller believed was queued (no outbox, no
   at-least-once delivery guarantee).
2. A skill's identity was two free-text fields (`skill_name`/
   `skill_version`); nothing stopped the actual `SKILL.md` prompt or
   `output.schema.json` contract behind a given "version" from silently
   drifting, invisibly poisoning the cache and the audit trail.
3. `bridge/noc_bridge/service.py` retried every job failure identically
   (a bare `except`), burning attempts (and, for a retired provider-invoking job,
   real dollars) reproducing permanent failures like an unsupported skill
   or an invalid schema.
4. Every credential in `apps/api/app/core/config.py` ships a convenient,
   publicly-known local-dev default with nothing stopping that same
   default from silently reaching a real deployment.

## Decision

**Batch A — transactional outbox + job lease** (already implemented
before this ADR was written): `enqueue_job` now writes `Job` +
`OutboxEvent` in one DB transaction; a background dispatcher
(`app/outbox.py`/`app/outbox_worker.py`) publishes pending events to
RabbitMQ afterward, so a mid-request crash leaves a durable, replayable
outbox row instead of a silently-dropped job.

**Batch B — Skill Registry.** A skill's real identity is now the sha256
content hash of its `SKILL.md` + `output.schema.json` + `skill.yaml`
(`apps/api/app/skills/registry.py::compute_skill_hash`), recorded as an
immutable `SkillSnapshot` row the first time a given hash is seen
(`get_or_create_snapshot`). `skill_hash` is stamped onto `Job`/
`AnalysisRun`/`Report` and threaded through the RabbitMQ job payload;
`bridge/noc_bridge/skill_registry.py` mirrors the hashing logic (same
separate-deployable-kept-in-sync-by-hand convention as `noc_bridge/db.py`/
`noc_bridge/config.py`) and verifies, immediately before executing a job,
that the skill's current on-disk content still matches the hash stamped
at enqueue time — raising a terminal `SkillHashMismatch` on drift (e.g. a
mid-deploy skill edit, or a job that sat in the DLX retry queue across a
skill change). Editing a skill file therefore produces a new hash, a new
snapshot, and an automatic cache-invalidation with no manual version bump.

**Batch C — failure classification, closing two gaps found in Phase 0
follow-up.**
- `bridge/noc_bridge/failures.py::classify_failure` gives `service.py` one
  place to decide RETRYABLE vs TERMINAL instead of a bare `except`:
  `UnsupportedJobType`, `OutputValidationError`, and `SkillHashMismatch`
  are terminal (retrying reproduces the identical failure); a
  `ChecksumMismatch` and an otherwise-unclassified `RuntimeError` are
  retryable up to `MAX_ATTEMPTS`.
- `_RETRYABLE_MESSAGE_MARKERS` had referenced a
  `structured_output_retry_exhausted` message that nothing in
  `sandbox/entrypoint.py` actually produced. `_invoke_retired-runtime` now retries
  a malformed/unparseable structured CLI result once at the same
  model/effort before raising that exact `RuntimeError` message — a single
  bad JSON parse no longer fails the whole job, but it also isn't retried
  indefinitely at the sandbox level (a fresh job-level retry, classified
  RETRYABLE, gets its own two CLI attempts).
- Confirmed `bridge/noc_bridge/queue_topology.py`'s hand-duplicated
  RabbitMQ topology constants are actually in sync with
  `apps/api/app/core/queue.py`, and added a permanent drift-guard test
  (`bridge/tests/test_queue_topology_drift.py`) so future divergence fails
  CI instead of relying on manual review at edit time.
- Fixed a real, reproducible ~1-in-3 test race
  (`test_request_analysis_exact_cache_hit_skips_queue`): the async outbox
  dispatcher could publish a prior job's message *after* the test's queue
  purge, making the "no message published for a cache hit" assertion
  flaky. Now deterministically drained first.

**Batch D — Skill Admin activation + credential hardening.**
- A new `skill.manage` permission (Admin role only) backs
  `GET /admin/skills`, `GET /admin/skills/{name}/versions`, and
  `POST /admin/skills/{name}/versions/{version_label}/activate`
  (`apps/api/app/skills/registry.py::set_active_snapshot`), fully
  audit-logged via the existing `record_audit`. This is explicitly an
  audit/bookkeeping control, not an execution override: a job always
  dispatches against whatever content is actually on disk, verified
  against the enqueue-time hash by the bridge regardless of which
  snapshot is flagged "active" — "activate" lets an operator flag a known-
  bad prompt version as not-endorsed without being able to silently
  rewrite what a past job actually ran.
- `apps/api/app/core/config.py::assert_production_secrets_are_safe`,
  called once at API startup (`main.py`'s lifespan): refuses to start
  (raises, doesn't just log) when a new `environment` setting is
  `"production"` and any of `jwt_secret`/`minio_secret_key`/
  `rabbitmq_url` is still at its known local-dev default. Never enforced
  in development, where those defaults exist on purpose for a zero-config
  local run.

## Consequences

**Easier:** a dropped job after a crash is now a replayable outbox row,
not silent data loss. A skill edit can no longer silently poison the
cache or the audit trail. Job failures stop wasting retry budget (and, for
retired provider-invoking jobs, real dollars) on permanent failures. A production
deployment can no longer boot on the dev JWT secret by accident. Skill
version history is queryable and one version can be flagged as endorsed
without touching what already ran.

**Harder / deferred:** `set_active_snapshot` is bookkeeping only — there
is still no mechanism to make the bridge actually *execute* an older
snapshot's content when disk content has moved on (would require either
snapshotting content into a bridge-readable location per version, or
accepting that a true rollback means reverting the on-disk files
themselves). No login rate-limiting/brute-force protection exists yet —
`auth.py`'s failed-login audit trail is visibility, not prevention. The
production-secrets check only covers the three credentials most likely to
be copy-pasted from this repo's own dev defaults; it is not an exhaustive
secrets audit.
