# ADR 0001: Milestone 0.5 Claude Bridge Spike — scope and stand-in boundary

- Status: accepted
- Date: 2026-09-09

## Context

Per Decision Log #2, Milestone 0.5 exists to de-risk the Docker sandbox
lifecycle (spawn, mount, limits, force-remove) before 11 milestones of UI
work are sunk, without pulling forward the full Claude Bridge (Milestone 12):
RabbitMQ consumption, MinIO transfer, and Postgres job records are explicitly
out of scope here.

A real `claude` CLI invocation inside the sandbox additionally requires
mounting live account credentials into an ephemeral container — a decision
about credential scoping that the master plan defers to Milestone 12's
design (§27 Bridge Responsibilities, §28 secrets guidance: "use minimum
required credentials", "avoid making backend credentials available inside
the sandbox").

## Decision

The spike's sandbox entrypoint (`sandbox/entrypoint.py`) is a deterministic
stand-in for the real Claude Code CLI call: it reads the mounted input log
and skill file, and returns a structured JSON result matching the
`log-triage-summary` skill's real output contract. It proves every mechanical
piece of the sandbox lifecycle (non-root user, `cap-drop=ALL`,
`no-new-privileges`, read-only root filesystem, tmpfs-only writes, CPU/memory/
PID limits, read-only input/skills mounts, writable output mount, force
removal) with a real Docker Engine, not the model call itself.

Wiring a live, credentialed `claude` CLI call replaces only
`run_skill()`'s body in Milestone 12, once credential scoping for the bridge
service is deliberately designed — not bolted onto this spike.

## Consequences

- Milestone 0.5 exit criteria are met without touching account credentials.
- Milestone 12 has a concrete, tested mount/limits/removal contract
  (`bridge/noc_bridge/sandbox_runner.py`) to build the real invocation on top
  of, rather than starting from zero.
- The skill-versioning question (Decision Log #3) is still open; this spike
  identifies the invoked skill by directory name only
  (`skill_invoked: "log-triage-summary"` in the output), not yet a hash or
  semver — revisit at Milestone 12 as already flagged.
