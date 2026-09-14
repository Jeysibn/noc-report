# Phase 9 implementation notes

## Initial review classification

- **CONFIRMED:** per-attempt maximum was used for `paid_ai_calls_used`; Shift Report used `limit: 10` without shift scope; effort defaulted to Medium; provenance was emitted by DOCX analysis rendering; browser tokens used localStorage and logout was stateless.
- **PARTIALLY CONFIRMED:** AI policy was split between bridge/database/frontend; the report already froze the correct shift incident set; `BilingualFindList` was implemented by adapters but absent from the Python parser/Block union; bilingual output was semantically bilingual but not language-section ordered.
- **ALREADY FIXED:** immutable snapshots, deterministic composition, full stored analysis source, screenshot integrity, natural Alerts pagination, and shared ReportDocument path.
- **NOT PRESENT:** no repository copy of the canonical DOCX was available for pixel comparison.

## Implemented invariants

### AI usage governance

`bridge/noc_bridge/ai_governance.py` owns effort resolution and delegates atomic Job budget operations. Reservation returns only newly available permits. Consumption adds the current sandbox attempt to the durable total and lowers the crash fence only after reconciliation.

Invariant:

```text
paid_ai_calls_used + active_reserved_permits <= paid_ai_call_budget
```

The cumulative total never decreases. A Job with budget 4 consuming 2 calls twice reaches 4; a third reservation returns 0.

### Effort

Shift Report uses `System default / Auto`; it omits `effort` unless LOW or MEDIUM is explicitly selected. The bridge resolves omitted effort from `system_config.default_effort`, currently LOW.

### Shift scope

`app.incident_scope.shift_incident_statement()` is shared by readiness listing and report snapshot freezing. `limit=0` means all matching incidents for readiness, avoiding the former ten-row mismatch.

### Presentation/provenance

Visible `ReportDocument.metadata` contains only Date and Shift. Audit fields are frozen in `ReportDocument.provenance`, per-analysis provenance, the database, and ReportSnapshot. Normal DOCX and preview JSON omit them; `render_document(..., include_provenance=True)` is the explicit audit path.

### Bilingual presentation

`AnalysisPresentation` now emits ordered single-language blocks:

```text
Chinese
  Short Summary
  Key Finds
  Secondary Finds
English
  Short Summary
  Key Finds
  Secondary Finds
```

### Authentication

Access tokens are memory-only in the browser. Refresh credentials are opaque, hashed server-side sessions in an HttpOnly, SameSite=Lax cookie, rotated on refresh and revoked on logout. Secure is enabled automatically for production. SameSite is the current CSRF boundary; future OIDC/Entra adoption is documented in ADR 0016.

## Verification

- Bridge report/document/governance tests: 24 passed; bridge non-integration suite: 65 passed.
- Full API suite: 107 passed.
- Sandbox suite: 53 passed.
- Web TypeScript/build, lint, and frontend suite: 20 tests passed; lint is clean.
- Python compilation and `git diff --check`: passed.
- Full API/bridge integration requires the repository’s real Postgres/RabbitMQ/MinIO setup. The local database has previously been inconsistent (`alembic_version` without the referenced tables), so destructive database repair was not performed.
