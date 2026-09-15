# ADR-0019: Phase 12 transactional invariants

- Status: Accepted
- Date: 2026-09-16
- Supersedes: none

## Decision

PostgreSQL is the final authority for the two Phase 12 identity rules:

1. `uq_shifts_one_active` is a partial unique index over `shifts.state` for
   `state = 'active'`.
2. `uq_reports_shift_version` uniquely identifies a report by
   `(shift_id, version)`.

The Shift lifecycle translates a unique-index race into a deterministic 409.
Report generation locks its Shift row with `FOR UPDATE` while allocating the
next version, so concurrent generations receive consecutive versions without
an external lock manager.

The migration must be applied before serving the new runtime. Existing data
must be inspected for duplicate active shifts or duplicate report versions;
the migration intentionally fails rather than silently changing operational
history.

## Consequences

Database enforcement remains effective across API processes, retries, and
worker restarts. A caller that loses an open-shift race reloads current state.
Report version allocation is serialized only per Shift, preserving concurrency
between unrelated shifts.
