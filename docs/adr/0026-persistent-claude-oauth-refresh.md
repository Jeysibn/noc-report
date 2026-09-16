# ADR 0026: Persist Claude OAuth Refresh State for the Bridge

- Status: accepted
- Date: 2026-09-17

## Context

The bridge previously copied `~/.claude/.credentials.json` into a temporary,
read-only directory once per bridge process. Claude Code could not persist an
OAuth access-token refresh into that copy, and the refreshed state was deleted
when the sandbox or bridge exited. This caused otherwise healthy bridge
installations to require repeated interactive logins.

## Decision

The bridge owns a persistent directory at
`~/.cache/noc-report/claude` by default. Before each Claude job, a newer host
login is copied into that directory. If the cached copy is newer, it is
retained because it may contain a refresh performed by Claude Code inside a
previous sandbox. Only that directory is mounted into the sandbox, and only
the credential file is present. The mount is writable so Claude Code can
persist token rotation; the operator's full `~/.claude` directory is never
mounted.

The cache is controlled by `BRIDGE_CLAUDE_CREDENTIALS_CACHE_PATH` and must be
protected with mode `0700`, with the credential file at mode `0600`. A host
re-login remains authoritative when its source file has a newer modification
time.

## Consequences

- Normal OAuth refreshes survive sandbox and bridge restarts.
- A bridge restart is no longer required after every refresh, although it is
  safe and still useful for operational recovery.
- The cache is a sensitive credential and must be included in host access
  control reviews, but it is not exposed to the API, browser, or other
  containers.
- If the OAuth refresh token itself is revoked or expired, an interactive
  `claude auth login` is still required; no service can safely bypass that.
