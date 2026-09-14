# ADR 0016: Phase 9 production hardening

## Status

Accepted

## Decisions

- Paid Claude usage is governed through one bridge seam and a transactional Job counter. `paid_ai_calls_used` is cumulative; reservations are a crash fence and never allow `used + reservation` to exceed `paid_ai_call_budget`.
- Daily Report effort is System Default/Auto in the UI. A missing override remains `NULL` in the Job message and the bridge resolves the configured system default, currently LOW.
- Shift readiness and ReportSnapshot creation share the shift-scoped incident statement. Readiness requests use `limit=0` to retrieve the complete scope.
- `ReportDocument.metadata` is operator-facing. `ReportDocument.provenance` and analysis provenance remain internal and are rendered only with explicit audit mode. Preview JSON excludes it.
- Current log analysis presentation emits an ordered Chinese section followed by an ordered English section. Single-language `FindList` is a supported generic ReportDocument block.
- Refresh credentials are opaque server-side sessions in an HttpOnly, SameSite cookie. Refresh rotates and revokes the old session; logout revokes the current session. Access tokens remain memory-only in the browser.

## Security note

The refresh cookie is Secure automatically when `environment=production`; local development remains HTTP-compatible. SameSite=Lax is the CSRF boundary for the refresh/logout POST endpoints. A future OIDC/Entra deployment should replace local password/session issuance while preserving the API’s permission model.
