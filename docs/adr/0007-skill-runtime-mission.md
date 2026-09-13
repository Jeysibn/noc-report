# 0007 — Skill Runtime, Reproducibility & Reliability Hardening

- Status: **implemented**
- Date: 2026-09-13

## Context

By ADR 0006, the platform reliably delivered jobs and could detect when a
skill's content drifted mid-flight (`SkillHashMismatch`). What it still
could not do was let a skill actually *govern* runtime behavior: analysis
output shape, report structure, and validation rules were still
partially hard-coded in Python (sandbox schemas duplicating
`output.schema.json`, bridge validation branching on `skill_name`, a DOCX
renderer with `daily-alert-report`-shaped field names baked in). Changing
a skill's output format meant editing Python in three places, not editing
the skill.

The mission's goal: make **create/edit skill → update schema → test →
activate SkillSnapshot → new jobs use new format** possible without
touching sandbox Python schemas, bridge validation code, database tables,
RabbitMQ orchestration, or renderer field names — infrastructure stays
generic and skill-owned concerns move fully into skill content.

## Decision

Twenty-one steps, executed in order, each verified against the full test
suite before proceeding (apps/api, bridge non-live, sandbox):

**Skill execution truth (1-6).** `SkillSnapshot` (ADR 0006) becomes
genuinely executable: an `is_active` flag controls which snapshot new
`Job`s are stamped with; the sandbox's hard-coded output schemas are
removed in favor of reading `output.schema.json` straight off the
mounted skill directory (`sandbox/entrypoint.py::_load_output_schema`);
the bridge's skill-specific validation branches collapse into one
generic `validate_output` against whatever schema the skill ships; skill
manifests (`skill.yaml`) gain `execution_policy` (timeout, effort) and
`input_contract` fields read generically rather than assumed per skill
name.

**Provenance (7-9).** Every `AnalysisRun` and `Report` records exactly
which `SkillSnapshot` (by hash) produced it, not just a human-chosen
version label. `ReportDocument` — a typed intermediate block model
(`Heading`, `Paragraph`, `BilingualText`, `IncidentEvidence`,
`AnalysisReference`, `Metadata`) — is introduced as the boundary between
"whatever a report skill's output happens to contain" and "what the DOCX
renderer draws," so the renderer needs no per-skill field-name knowledge
(`render_document` in `bridge/noc_bridge/docx_render.py`).

**Protocol and lifecycle correctness (11-16).**
- The outbox dispatcher (ADR 0006 Batch A) now claims one row per
  transaction rather than a batch, closing a double-publish race.
- `outbox_mode` (`embedded`/`external`) makes explicit whether the API
  process runs its own background dispatcher thread or defers entirely
  to a standalone `python -m app.outbox_worker`, so a real deployment can
  run exactly one dispatcher instead of racing an embedded copy inside
  every replica.
- A job's Postgres claim lease now covers its configured
  `job_timeout_seconds` plus a 120s safety margin
  (`bridge/noc_bridge/service.py::_LEASE_SAFETY_MARGIN_SECONDS`) instead
  of a fixed shorter lease that could expire mid-execution and let a
  second worker reclaim a still-running job.
- The RabbitMQ job message envelope has one canonical schema
  (`packages/contracts/job_message.schema.json`), validated by both the
  producer (`apps/api/app/core/queue.py::build_job_message`) and the
  consumer (`bridge/noc_bridge/service.py`, routing a schema-invalid
  message straight to the DLQ instead of crashing the worker),
  eliminating the two-hand-kept-in-sync-copies risk.
- Deterministic log-pattern extraction (exact per-pattern counts,
  severity flags) now runs for every log regardless of size
  (`sandbox/entrypoint.py::_deterministic_stats_appendix`), not only for
  logs large enough to need compaction — so count/percentage grounding
  is uniformly exact, never a mix of exact-for-large/estimated-for-small.
- The analysis cache key drops the redundant `skill_version` label filter
  and keys purely on `skill_hash` — the mechanically-derived identity
  that cannot drift independently of the content it names.

**Hardening (17-18).**
- The sandbox's mounted input/output/credential directories no longer
  get a blanket `chmod(0o777)`/`0o755`/`0o644`. Since Docker bind mounts
  preserve host ownership and the container's fixed non-root uid (10001)
  is neither the owner nor a group member of anything the host creates,
  the only lever available without root is the "other" permission bits;
  each directory now gets exactly the bits its actual access pattern
  needs (input: other=r-x/r--; output: other=-wx only, no read, since the
  host process already owns and reads its own output; the copied OAuth
  credential: other=r-- only) and all "group" bits are zeroed
  (`bridge/noc_bridge/sandbox_runner.py::_grant_sandbox_uid_access`,
  `bridge/noc_bridge/credentials.py`).
- CI's `minio/minio:latest` service image is pinned to a specific
  release tag (`.github/workflows/ci.yml`), removing an unpinned
  dependency that could silently change CI behavior with no
  corresponding commit.

**Proof and documentation (19-21).** A synthetic skill with an output
shape sharing nothing with either real skill (no `summary`, no
`sections`, no bilingual fields) runs end-to-end through
`sandbox/entrypoint.py::run_skill` with zero sandbox code changes
(`sandbox/tests/test_preprocessing.py::test_run_skill_handles_a_synthetic_skill_with_an_unrelated_output_shape`),
complementing the existing renderer-layer proof
(`bridge/tests/test_report_document.py::test_render_document_is_generic_over_a_synthetic_document_shape`)
that a differently-shaped `ReportDocument` renders through the same DOCX
adapter. Together these are the mission's central claim made concrete:
a format change is a skill-content change, not a Python change.

## Consequences

- Editing a skill's `SKILL.md`/`output.schema.json`/`skill.yaml` changes
  its hash, which changes its `SkillSnapshot`, which automatically
  invalidates the analysis cache and produces a new, independently
  auditable provenance trail — with no manual version bump and no code
  review of sandbox/bridge/renderer Python required for a pure format
  change.
- The permission tightening in step 17 is bounded by what Docker bind
  mounts allow without root: "other" bits are still granted (the
  container's uid cannot be predicted or chowned to ahead of time
  without deeper changes such as user-namespace remapping or rootless
  Docker with matching subuid ranges), but the granted bits are now the
  minimum each mount's actual read/write direction requires, and no
  "group" bit is ever granted.
- `outbox_mode=external` requires an operator to actually run
  `python -m app.outbox_worker` as its own process in that deployment
  topology; leaving it at the `embedded` default in a topology that also
  runs a standalone dispatcher would reintroduce the double-dispatch race
  this ADR closes — this is a deployment configuration responsibility,
  not something the code can enforce from inside either process.
