# Phase 11 implementation report

## Outcome

The inspected `main` branch was already substantially reconciled by the
Phase 9 and Phase 10 changes. No confirmed regression was found in the core
architecture listed in the Phase 11 brief. Phase 11 therefore hardens the
release path around the existing implementation instead of redesigning it.

## Baseline coherence matrix

| Area | Runtime | Contract/schema | Tests | Initial status |
| --- | --- | --- | --- | --- |
| Authentication | HttpOnly opaque refresh session, rotation, revoke, logout | `TokenPair` contains access token only; cookie contract in auth router | API auth tests cover cookie, rotation, reuse rejection, logout, RBAC | ALREADY FIXED |
| Shift readiness | Shared `shift_incident_statement`; readiness requests `limit=0` | `IncidentPage` carries complete `total`; `shift_id` filter | 14 incidents in Shift A vs 6 in Shift B, snapshot equality | ALREADY FIXED |
| ReportSnapshot | Freezes shift facts, evidence, analyses, policy, timezone | `snapshot_json` and SHA-256; `shift_timezone` | Manila/UTC/DST and historical freeze tests | ALREADY FIXED |
| AI budget | Atomic reservations plus cumulative `record_paid_ai_calls` | Job budget/reserved/used columns and migrations | 2 + 2 calls reaches 4; fifth reservation returns 0 | ALREADY FIXED |
| Model/effort | Bridge AI governance resolves omitted Auto values from `system_config` | nullable request overrides; durable effective policy | Auto and explicit override tests | ALREADY FIXED |
| ReportPlan | retired provider produces narrative only; composition derives coverage | Daily Report schema requires bilingual `general_summary` | missing/empty summary and narrative composition tests | ALREADY FIXED |
| AnalysisRun | Application transition plus PostgreSQL partial unique index | Alembic `f9a0b1c2d3e4` | current-run invariant and duplicate repair coverage | ALREADY FIXED |
| ReportDocument | Deterministic `Alerts → General Summary → Log Analysis` | renderer-neutral JSON contract | DOCX/Web composition, fidelity, pagination tests | ALREADY FIXED |
| CI/release coherence | Existing component jobs plus new static gate | `scripts/check_coherence.py` | gate reproduces contract assumptions | PARTIALLY CONFIRMED |
| Sandbox CI invocation | Tests require `sandbox` on import path | CI now sets `PYTHONPATH=sandbox` | command previously failed collection locally | CONFIRMED |

## Finding classification

- **CONFIRMED:** the CI sandbox job omitted `PYTHONPATH=sandbox` and failed to
  import `entrypoint`.
- **PARTIALLY CONFIRMED:** coherence was present in distributed tests and
  ADRs, but there was no fast explicit branch-coherence CI gate.
- **ALREADY FIXED:** secure auth, shift-scoped readiness, frozen timezone,
  cumulative AI accounting, Auto model/effort, narrative-only plans,
  substantive bilingual summary validation, current-run uniqueness, and
  canonical full-fidelity report composition.
- **NOT PRESENT:** no evidence of the prompt's obsolete body refresh JWT or
  `localStorage.refresh_token` behavior in the inspected checkout.

## Contract proofs

### Authentication

Before the Phase 9 reconciliation, the documented drift was body refresh JWTs,
browser storage, and stateless logout. Current `main` uses an opaque secret in
an HttpOnly SameSite cookie, stores only its SHA-256 hash in
`refresh_sessions`, locks and revokes the old row on refresh, rotates the
secret, and revokes/clears it on logout. The frontend only keeps the short-lived
access token in memory and sends `credentials: "include"`.

### Shift/report scope and timezone

Readiness and `_build_snapshot()` both call the same shift-scoped statement.
The readiness UI requests `limit=0`, so Shift A with 14 incidents reports all
14, while Shift B's 6 incidents are excluded. The report snapshot uses the
same incident IDs. The snapshot also freezes the IANA timezone: `17:00Z` on
2026-09-14 is `01:00` on 15 September in `Asia/Manila`, and the report date is
15 September 2026.

### AI budget and Auto policy

For budget 4: attempt 1 consumes 2, attempt 2 consumes 2, durable
`paid_ai_calls_used` is 4, and the next reservation is 0. Reservation rows are
locked atomically; uncertain worker crashes retain their reservation fence.
With no request override, the bridge resolves `system_config.default_model`
and `default_effort`; explicit model/effort values win.

### ReportPlan and summary

The canonical schema has only required bilingual `general_summary` and optional
cross-incident narrative. It does not ask retired provider for 10 incident IDs and 10
analysis IDs. Composition still materializes every frozen alert and available
analysis. Missing, empty, or whitespace-only `zh`/`en` values fail validation;
substantive bilingual values pass.

### AnalysisRun and report fidelity

Migration `f9a0b1c2d3e4` repairs legacy duplicate current rows and creates the
partial unique index on `(incident_id) WHERE current IS TRUE`. The canonical
document keeps all stored `AnalysisPresentation` detail, including Key Finds,
Secondary Finds, counts, percentages, technical identifiers, screenshots,
Grafana links, and exact filenames, in `Alerts → General Summary → Log Analysis`
order. Alerts have no arbitrary ten-item cap.

## Verification

The repository CI order was rerun locally after applying migrations:

- API: 108 passed.
- Bridge: 79 passed, 2 skipped (live retired provider and Docker-dependent test gates).
- Sandbox: 53 passed.
- Frontend: 21 passed; lint and production build passed.
- Alembic: upgraded cleanly to `f9a0b1c2d3e4`.
- Coherence gate: passed.

The initial bridge failure was caused by concurrently running the API fixture,
which drops/recreates all tables, against the same local database. Running the
repository's isolated CI order with migrations removed that environmental
failure.

## Documentation and operational impact

- Added `scripts/check_coherence.py` and the `check:coherence` npm command.
- Added the CI coherence job.
- Fixed the sandbox CI import path.
- Added ADR 0018 documenting the gate and the supersession boundary for older
  reference-heavy report-plan decisions.
- No database migration is required by Phase 11; the existing head remains
  `f9a0b1c2d3e4`.

## Remaining risks

- **P0:** none identified in the inspected scope.
- **P1:** live retired provider/Docker end-to-end paths remain opt-in/skipped in normal
  CI and must be exercised during an operational release rehearsal.
- **P2:** the static coherence gate is intentionally narrow; contract changes
  still require focused tests and migration review.
- **Future:** a browser-level security test could assert cookie attributes
  through a real deployed HTTPS origin; current API tests and source gate
  cover the implemented contract.
