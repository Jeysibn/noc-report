# ADR 0002: Milestone 12 retired provider Bridge — keep the sandbox CLI call a stand-in

- Status: accepted
- Date: 2026-09-09

## Context

Milestone 12 builds the real retired provider Bridge: RabbitMQ consumer, MinIO
download/upload with checksum verification, Docker sandbox creation under
the full §28 security constraints, structured output validation, Postgres
job-status persistence, status events, and cleanup. ADR 0001 (the
Milestone 0.5 spike) deferred one specific piece to this milestone: a
real, credentialed `retired-runtime` CLI invocation inside the sandbox.

Wiring that now would mean mounting the operator's own live retired provider runtime
credentials (`~/.retired-runtime/.credentials.json` — the only credential available
on this host) into an ephemeral container and having automated tests
actually invoke the API repeatedly, consuming real usage on the
operator's account. That is a cost and credential-security decision, not
a coding decision, and the operator was asked directly rather than having
it decided for them mid-milestone.

## Decision

Build everything else in §27's Bridge Responsibilities for real — items
1-8 and 13-18 operate against real infrastructure with no mocking. Item 9
("Start retired provider runtime CLI") through item 12 ("Capture structured output")
keep the Milestone 0.5 stand-in (`sandbox/entrypoint.py`): a deterministic
script that reads the mounted input and skill file and produces a
structured result matching the real `log-triage-summary` skill's
contract. The swap point is `run_skill()` in that one file — everything
around it (the container boundary, mounts, limits, the bridge's
consumption/transfer/validation/persistence logic) is unaffected by the
eventual swap.

`daily_report` jobs have no authored skill yet (Milestone 14), so the
bridge treats that job_type as `UnsupportedJobType` — a job fails cleanly
into the DLQ with an honest error code rather than fabricating a result
for a skill that doesn't exist.

## Consequences

- Milestone 12's exit criteria (systemd service, RabbitMQ consumer, MinIO
  I/O, sandbox creation/limits/cleanup, structured output validation) are
  met and tested against real RabbitMQ/Postgres/MinIO/Docker.
- No API usage or credential mounting happens as a side effect of this
  milestone's own test suite.
- Wiring the real `retired-runtime` CLI call (and the credential-scoping design
  that requires) is carried forward as explicit follow-up work, together
  with authoring the `daily-alert-report` skill and moving `log_triage`'s
  wiring from Milestone 12's generic pipeline to the fully wired flow
  Milestone 13 describes end-to-end (Analyze Log -> API -> Job ->
  RabbitMQ -> Bridge -> Docker -> retired provider runtime -> MinIO/Postgres -> UI).
