# ADR-0025: Evidence purge safety, report artifact identity, and serial bridge capacity

- Status: Accepted
- Date: 2026-09-17
- Supersedes: deletion ordering assumptions in ADR 0021

## Context

MinIO mutation before a database commit could destroy bytes and then be rolled
back by a foreign-key failure. Generated report rows also did not retain the
MinIO version ID, even though evidence and report buckets are versioned. The
bridge callback processes one delivery synchronously, while the admin setting
allowed a larger number that looked like execution parallelism.

## Decision

Evidence deletion is database-first. Referenced evidence remains protected;
unreferenced evidence is marked `PURGE_PENDING` in a committed transaction,
then the exact bucket/key/version is deleted and the tombstone is marked
`PURGED`. Incident deletion detaches the tombstones before removing the
incident and follows the same cleanup behavior.

Report rows pin bucket, key, version, checksum, byte size, and content type.
Downloads use the pinned version and verify the checksum. Existing unpinned
rows are legacy and must be reconciled before download.

The bridge remains intentionally serial. The shared AI execution policy
contract limits effective capacity to one and both API and bridge use
`prefetch_count=1`. A future parallel worker requires a separate decision
covering sandbox, credentials, resource limits, leases, and shutdown.

## Consequences

Storage cleanup may be pending after a dependency outage, but database state
never claims that an active evidence object still exists after this request
has deleted it. Historical reports remain reproducible. Operators see an
honest capacity value instead of a misleading concurrency control.
