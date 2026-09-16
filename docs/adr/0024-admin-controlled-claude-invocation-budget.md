# 0024 — Admin-controlled Claude invocation budget

- Status: **accepted**
- Date: 2026-09-16

## Context

The Claude CLI has a per-invocation `--max-budget-usd` guardrail. It was
previously fixed at `$0.50` in bridge process configuration, so a legitimate
analysis could terminate with `terminal_reason='budget_exhausted'` and the
operator had no runtime control from NOC Report.

This is separate from the durable per-Job paid-call budget. The latter limits
how many Claude invocations a job may consume; this setting limits the spend
allowed by each individual CLI invocation.

## Decision

Persist `claude_max_budget_usd` in the single-row `system_config` table. An
administrator with `system.configure` may set it from `$0.01` through `$10.00`
in the Admin → AI configuration page. The bridge reads the value from
PostgreSQL for every dispatch and passes it to the sandbox as
`SKILL_MAX_BUDGET_USD`; no bridge restart is required.

The bridge’s environment setting remains a fallback for a missing configuration
row, and the API validates the upper bound to preserve a cost-safety ceiling.

## Consequences

- Operators can raise the cap for a complex analysis and lower it afterward.
- Existing jobs retain their durable accounting and are not retroactively
  changed; the setting applies to the next dispatched job.
- This does not change Claude model selection, effort, retries, or the separate
  paid-call reservation budget.
