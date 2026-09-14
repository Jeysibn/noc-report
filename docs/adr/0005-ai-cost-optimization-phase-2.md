# 0005 — AI Usage & Cost Optimization, Phase 2

Status: **implemented** (Issues 1-7, 9-10 below), with Issue 7 and Issue 8
explicitly scoped down from the full mission brief — see "Deliberate
scoping" at the end. Supersedes/corrects two claims in ADR 0004 (see the
correction note at the top of that file).

Guiding principle carried over from the mission brief and unchanged:
**"Deterministic code computes facts. Claude interprets evidence."**
Preserves the existing architecture (React → FastAPI → Postgres/MinIO →
RabbitMQ → Host Bridge → Docker Sandbox → Claude Code CLI), Claude
subscription OAuth auth (never `--bare`, never API-key billing), RabbitMQ,
MinIO, Postgres provenance, Docker sandbox isolation, RBAC, bilingual
analysis, independent per-log analysis, report snapshots, structured JSON
validation.

## Issue 1 — minimize Claude Code agent overhead

`sandbox/entrypoint.py`'s `_invoke_claude` now passes `--system-prompt`
(a short, skill-agnostic NOC-analyst identity string, `_NOC_SYSTEM_PROMPT`
— replacing the CLI's default coding-agent system prompt, which this
one-shot structured-output call never needed) and `--no-session-
persistence` (no transcript is ever resumed for a one-shot analysis job).

Both were verified as real, supported flags via `claude --help` and live
test calls before use. Two flags considered and deliberately **not**
added, with reasoning kept as inline comments in `entrypoint.py`:
- `--disallowed-tools "*"` — empirically breaks the CLI's own internal
  "StructuredOutput" tool call mechanism that `--json-schema` depends on
  (confirmed via a live test: it produces `permission_denials` and a
  total failure to return structured output). `--restricted` +
  `--permission-mode dontAsk` + `--permission-prompts none` (already in
  place pre-mission) already strip every *other* tool.
- `--max-turns` — does not exist on this CLI build (confirmed via
  `claude --help`); nothing to add.

Net effect: strictly less overhead sent to/kept by Claude per call, with
no loosening of the sandbox's own isolation (network/cap-drop/read-only-
rootfs/resource-limits are unchanged) — reducing Claude's own tool
surface here *is* the security improvement the mission required, not a
tradeoff against it.

## Issue 2 — structured-output parsing

`_parse_claude_result(envelope, *, schema)` now prefers, in order:
1. `envelope["structured_output"]` if it's a dict (the CLI's own already-
   schema-validated field);
2. `envelope["result"]`, parsed as JSON if it's a string, or used directly
   if it's already a dict;
3. otherwise raises `ValueError` — no silent fallback to a malformed or
   empty result.

Verified against all real envelope shapes seen from live `claude -p
--output-format json --json-schema ...` calls (both fields present
together in practice) — see `sandbox/tests/test_structured_output.py`,
8 cases including malformed/missing-field variants.

## Issue 3 — `default_effort` migration

`apps/api/alembic/versions/c3d4e5f6a7b8_migrate_default_effort_to_low.py`:
a real data migration (not just a Python-default change) updating any
existing `system_config.default_effort = 'medium'` row to `'low'`,
matching ADR 0004 Phase 4's policy. `downgrade()` is a documented no-op
(reverting silently would be a policy decision, not a schema one).
Verified via a real upgrade-from-scratch and a downgrade/upgrade
roundtrip against Postgres.

## Issue 4 — cumulative escalation telemetry

Previously, an escalated (retried at higher effort) run's telemetry
overwrote the initial attempt's numbers instead of summing them — a NOC
operator reading `input_tokens`/`estimated_cost_usd` on an escalated run
saw only the *second* call's cost, silently hiding the first.

`AnalysisRun` gains `initial_*` / `escalation_*` field pairs (model,
effort, input/output/cache tokens, duration, cost) alongside the existing
top-level fields, which now hold true **sums** via a local `_sum(*values)`
None-safe helper in `sandbox/entrypoint.py`'s `run_skill`. `num_turns` is
deliberately *not* summed — turns aren't meaningfully additive across two
independent CLI invocations, so it's just the escalated call's own value.
`attempt_count` records 1 or 2.

Regression tests: `sandbox/tests/test_escalation.py::
test_run_skill_escalation_telemetry_is_cumulative_not_overwritten` and
`test_run_skill_non_escalated_telemetry_totals_equal_initial`.

## Issue 5 — cache versioning + operator-override respect

The historical implementation used `ANALYSIS_SCHEMA_VERSION` together
with preprocessing and policy constants. Phase 5 removes that duplicated
schema version from the runtime contract: the immutable
`SkillSnapshot.content_hash` covers `SKILL.md`, `skill.yaml`, and
`output.schema.json`. The remaining cache contract covers only
application-owned preprocessing and policy axes, and is stored on every
new `AnalysisRun`.

`_find_cached_analysis_run` also now takes the request's own
`requested_model`/`requested_effort` and only filters on those columns
when the caller actually specified them — an operator's explicit
model/effort override is respected (never silently served a cached result
computed under different settings), while a request with no explicit
override still benefits from cross-model/effort cache hits as before.

Tests: `apps/api/tests/test_analysis.py` — contract-version match/
mismatch, skill-version mismatch, explicit-override-respected cases.

## Issue 6 — deterministic daily-report assembly

See the correction note at the top of ADR 0004 for what was actually
wrong here. Now:

- Historical `daily_report_docx` snapshots used a compact per-incident
  summary (`display_id`, `title`, `status`, `severity_signal`, `main_error`,
  `impact`, `starts_at`, `ends_at`) and deterministic bridge-side merging.
  The active daily-report snapshot now uses the manifest's declarative input
  projection and emits the skill-owned compact `ReportPlan` contract. The
  bridge's deterministic Report Composition module resolves evidence and
  analysis references into `ReportDocument`; the old assembler remains only
  for queued/historical snapshots whose immutable manifest selects that
  profile.
- For historical snapshots, `bridge/noc_bridge/service.py`'s
  `_merge_daily_report(snapshot,
  ai_output)` deterministically rebuilds the full `title`/`sections[]`
  report structure `docx_render.py` and the (unchanged) full-shape
  `_validate_daily_report` expect, carrying every per-incident field
  through from the frozen snapshot verbatim and computing the title
  itself — none of it round-tripped through Claude.
- The historical compatibility profile keeps two-stage validation —
  `bridge/noc_bridge/validation.py`'s
  `_validate_daily_report_ai_output` checks Claude's raw compact output
  (wired as the `daily_report` entry in `_VALIDATORS`/`validate_output`);
  `validate_merged_daily_report` checks the final assembled report before
  rendering.
- The active declarative profile renders its skill-owned block order; the
  historical adapter renders the old "Cross-Incident Findings" section.

Tests: `sandbox/tests/test_daily_report_compaction.py` (compact-summary
extraction; proof the prompt actually sent to Claude excludes screenshots/
links/full analysis) and `bridge/tests/test_daily_report_merge.py`
(merge correctness, verbatim per-incident carry-through, two-stage
validation).

## Issue 7 — structured evidence engine (partial)

**Scoped down** from the full brief's Parser → Field Extraction →
Normalization → Event Grouping → Exact Statistics → Severity Ranking →
Representative Sampling → Compact Evidence pipeline running on *every*
log. What's actually implemented: the specific, concrete bug in
`sandbox/entrypoint.py`'s existing compaction path (which only engages
above `MAX_LOG_CHARS`) — a blanket `\d+` regex was collapsing
semantically distinct numbers (HTTP 403 vs 500, different business error
codes) into the same pattern signature, hiding real distinctions. Replaced
with field-aware patterns that normalize only genuinely dynamic values
(UUIDs, hex trace/span/correlation ids, IPv4, timestamps, `user_id`/
`record_id` values, thread/worker/pool suffixes, k8s pod hash suffixes)
and leave status/error codes, ports, and line numbers untouched.

Tests: 6 new cases in `sandbox/tests/test_preprocessing.py` (HTTP
403≠500, distinct business error codes, dynamic trace/user/thread ids
correctly normalized, root-cause stack frame surviving compaction).

**Not built:** always-on structured extraction below `MAX_LOG_CHARS`,
exact per-pattern statistics as a first-class evidence object (currently
implicit in the compaction's count-per-signature), and a general Event
Grouping/Representative Sampling pipeline independent of the size
threshold. See "Deliberate scoping" below for why.

## Issue 8 — A/B benchmark

`scripts/benchmark_preprocessing.py` (pre-existing) measures deterministic
compaction byte-size effects with no live calls. `scripts/
benchmark_live_ab.py` (new) runs the real `claude` CLI directly through
`sandbox/entrypoint.py`'s own `run_skill` — the exact prompt/schema/CLI
flags a real job uses — against synthetic fixture logs covering every
error type in the mission brief (NullPointerException,
DuplicateKeyException, DateTimeParseException, JSON parse failure,
BusinessException with Chinese text, HTTP 403, ClickHouse timeout,
Elasticsearch failure, JVM OOM, a repetitive-error log, multiple unrelated
errors, and a rare critical error hidden in noise), recording real
cost/tokens/duration/confidence/severity per fixture. Gated behind
`NOC_LIVE_BENCHMARK=1` (spends real subscription usage, same convention as
`bridge/tests/test_bridge.py`'s `NOC_BRIDGE_LIVE_CLAUDE_TESTS`). Results
from the run performed during this mission are recorded in
`scripts/benchmark_live_ab_results.json` and summarized below.

## Issue 9 — CI expansion

`.github/workflows/ci.yml` gained three jobs beyond the pre-existing
frontend-only `web` job: `api` (Postgres/RabbitMQ/MinIO services matching
`infrastructure/docker-compose.dev.yml`'s ports/credentials, runs
`apps/api/tests`), `sandbox` (no services needed, runs
`sandbox/tests` — preprocessing/escalation/telemetry/structured-output/
cache-version/daily-report-compaction), and `bridge` (same three services
+ `alembic upgrade head` + `bridge/tests`, with the one real-Claude-CLI
end-to-end test still gated off via an unset `NOC_BRIDGE_LIVE_CLAUDE_
TESTS`).

## Deliberate scoping

Given this mission's own priority order (1. correctness, 2. incident
safety, 3. reliability, 4. low AI usage, 5. auditability, 6. operational
simplicity), two items were scoped down rather than built to the letter
of the original brief, and this is stated plainly rather than silently
narrowed:

- **Issue 7** — the full always-on, size-independent evidence pipeline
  was not built. The existing compaction path only engages above
  `MAX_LOG_CHARS` (80,000 chars by default) by design (see ADR 0004's
  "not worth implementing" section, still valid): a log under that
  threshold is already cheap and compacting it risks losing verbatim
  detail for no measurable benefit. The field-aware normalization fix
  actually implemented addresses the concrete correctness bug the mission
  called out (distinct errors collapsing to one signature) without
  rebuilding a pipeline that, for most real logs, would never engage.
- **Issue 8** — the benchmark covers every fixture type requested with
  real Claude invocations and real measured numbers, but is a single
  representative run rather than a large statistical sample (each fixture
  run once, not N times per model/effort combination) — real subscription
  usage is spent per invocation, and the mission's own cost-minimization
  goal argues against a large exploratory sweep just to produce a
  benchmark artifact.
