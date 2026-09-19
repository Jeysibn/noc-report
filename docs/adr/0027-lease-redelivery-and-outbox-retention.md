# ADR-0027: Lease-aware RabbitMQ redelivery and published outbox retention

- Status: Accepted
- Date: 2026-09-19

## Context

RabbitMQ keeps a delivery unacknowledged while the bridge executes a Job, while
PostgreSQL records the bridge worker's lease. A worker can die after claiming a
Job but before ACKing its delivery. Treating every failed claim as a harmless
duplicate would ACK the only delivery while the lease was still live, leaving
the Job stranded after the lease expired.

Published transactional-outbox rows also need bounded operational retention,
without ever removing an unpublished event that still represents work.

## Decision

`claim_job` returns a classified result: `CLAIMED`, `ALREADY_COMPLETED`,
`LEASE_BUSY`, or `NOT_CLAIMABLE`. A live lease is temporary contention and its
delivery is NACKed without requeue so the existing dead-letter retry queue
delays it. Once the lease expires, the delivery can be reclaimed. Completed
duplicates ACK without another Claude call; terminal rows are quarantined to
the job DLQ. Lease contention does not increment the Job failure attempt.

The outbox dispatcher periodically deletes only rows whose `published_at` is
strictly older than the configured retention cutoff. Cleanup is bounded,
repeatable, and uses row locking; rows with `published_at IS NULL` are never
eligible. Retention days, batch size, and cleanup interval are deployment
configuration.

## Consequences

Bridge crashes no longer strand recoverable Jobs solely through broker/lease
state divergence, and healthy duplicate deliveries remain idempotent. The
outbox remains durable until publication is confirmed, while old published
history is eventually bounded without deleting pending work.
