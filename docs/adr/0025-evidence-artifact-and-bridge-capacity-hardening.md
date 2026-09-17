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
incident and follows the same cleanup behavior. The API's purge sweeper retries
pending tombstones with row locking and treats an already-missing exact
version as success, so a MinIO outage does not create a permanent orphan.

Report rows pin bucket, key, version, checksum, byte size, and content type.
Downloads use the pinned version and verify the checksum. Existing unpinned
rows are legacy and must be reconciled before download. The structured
`ReportDocument` preview and private screenshot index use the same identity
discipline: the bridge records each artifact's exact version and checksum on
the Report row before completion, and preview retrieval never reads latest at
a deterministic key.

The bridge remains intentionally serial. The shared AI execution policy
contract limits effective capacity to one and both API and bridge use
`prefetch_count=1`. A future parallel worker requires a separate decision
covering sandbox, credentials, resource limits, leases, and shutdown.

Report crash reconciliation uses one completion contract for normal and
redelivered success. It requires the complete deterministic artifact set for
the active ReportDocument renderer, reads each object's exact MinIO version,
recomputes checksum and size, and persists all identities before completing
the Job. Partial artifact sets are not marked complete.

Evidence purge sweeps exclude records that became protected during the
current sweep after rollback, preventing one old pending tombstone from
starving newer eligible records.

## Consequences

Storage cleanup may be pending after a dependency outage, but database state
never claims that an active evidence object still exists after this request
has deleted it. Historical reports remain reproducible. Operators see an
honest capacity value instead of a misleading concurrency control.
