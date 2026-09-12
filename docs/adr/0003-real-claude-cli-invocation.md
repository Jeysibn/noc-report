# ADR 0003: Wire the real Claude Code CLI into the sandbox, via OAuth subscription auth

- Status: accepted
- Date: 2026-09-09
- Supersedes: ADR 0002's stand-in scope decision

## Context

ADR 0002 deliberately deferred the actual "Start Claude Code CLI" /
"Capture structured output" steps (§27 items 9-12) behind a deterministic
stand-in, pending an operator decision on cost/credential handling. Asked
directly, the operator specified two things:

1. The sandbox must use their **Claude Code CLI subscription** for
   analysis — not a separate Anthropic API key. They do not have raw API
   access; only a Claude Code login.
2. Confirmed explicitly: usage should draw on their Claude Code
   subscription usage, not API billing.

This rules out the most obvious implementation (`ANTHROPIC_API_KEY` in
the container environment) and requires the sandbox to authenticate the
same way this very CLI session does: via the OAuth credential Claude Code
stores at `~/.claude/.credentials.json`.

## Decision

- **Auth**: the bridge copies the operator's `.credentials.json` (only
  that one file, never the rest of `~/.claude`) into a fresh temp
  directory with relaxed permissions (`noc_bridge/credentials.py`), and
  bind-mounts that directory read-only to `/home/sandbox/.claude` inside
  the container. The CLI is invoked **without** `--bare`, so it reads
  this OAuth credential rather than requiring `ANTHROPIC_API_KEY` (`--bare`
  strictly requires an API key/apiKeyHelper and never reads OAuth/keychain
  — the opposite of what's needed here).
- **Binary**: `~/.local/bin/claude` is a symlink to a versioned install
  path; the bridge resolves it (`Path.resolve()`) once at service startup
  and bind-mounts the real target read-only to `/usr/local/bin/claude`,
  so a host `claude` upgrade takes effect on the next job with no image
  rebuild.
- **Network**: `run_job_sandbox()` gained a `network_disabled` parameter
  (default `True`, unchanged for every other job type/step). Only the
  log_triage job type passes `network_disabled=False`, since the CLI
  needs to reach Anthropic's API. Every other §28 requirement (non-root
  uid 10001, cap-drop=ALL, no-new-privileges, read-only rootfs, tmpfs-only
  writes, CPU/mem/PID limits, execution timeout, no Docker socket, force
  removal) is unchanged — §28 doesn't mandate network isolation, so this
  is a documented exception, not a violation.
- **Invocation** (`sandbox/entrypoint.py`): `claude -p --output-format
  json --model <SKILL_MODEL> --permission-mode bypassPermissions
  --permission-prompts none --restricted --max-budget-usd
  <SKILL_MAX_BUDGET_USD> --json-schema <schema>`. `--restricted` strips
  tool-use surface (Bash, code execution) since this skill only needs
  text analysis. `--permission-prompts none` guarantees no hang waiting
  for an interactive prompt that can never be answered in a headless
  container. `--max-budget-usd` and a process timeout (`SKILL_CLI_TIMEOUT_
  SECONDS`, wired from `settings.claude_cli_timeout_seconds`) bound cost
  and runtime per job.
- **Cost guardrails outside the CLI itself**: `BridgeSettings.claude_max_
  budget_usd` (default $0.50/job) and `claude_cli_timeout_seconds`
  (default 280s, under the sandbox's own container timeout).
- **Tests**: `test_end_to_end_log_triage_job` now asserts only the
  schema-required output fields (not stand-in-only fields like the old
  `skill_invoked`), and is skipped by default — it spends real usage on
  the operator's subscription, so it requires `NOC_BRIDGE_LIVE_CLAUDE_
  TESTS=1` to opt in, and is meant to be run by the operator directly
  (e.g. via a `!`-prefixed shell command), not executed by Claude Code
  itself.

## A note on verification

I (Claude Code, building this) could not personally execute a live test
of the real `claude` CLI invocation via my own Bash tool: Claude Code's
own auto-mode classifier blocks a nested/recursive `claude` CLI
invocation as a matter of product policy, independent of this project's
own permissions. The exact shape of the CLI's `--output-format json`
envelope (in particular, whether the result payload is a raw string, a
nested object, or something else) was reasoned from `claude --help` text
only, not empirically confirmed. `entrypoint.py`'s parsing is written
defensively (handles both a wrapped and unwrapped JSON payload) but
should be verified against a live run by the operator before this path
is relied on in production, and the parsing may need adjustment based on
what that first real run shows.

## Consequences

- No `ANTHROPIC_API_KEY` is ever provisioned, read, or required anywhere
  in the bridge or sandbox.
- The only credential inside any container is a short-lived-permission
  copy of the operator's own OAuth token, scoped to one file, mounted
  read-only, and cleaned up (`atexit`) when the bridge process exits.
- Routine test runs (`pytest` with no env var set) spend zero real usage;
  only an explicit, operator-run opt-in does.
- `daily_report` jobs remain `UnsupportedJobType` (unchanged from ADR
  0002) until Milestone 14 authors that skill.
