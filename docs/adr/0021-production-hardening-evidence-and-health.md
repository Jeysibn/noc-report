# ADR-0021: Production hardening at storage, protocol, and health seams

- Status: Accepted
- Date: 2026-09-16
- Supersedes: none

## Context

The application already has immutable report and analysis concepts, but two
process boundaries still depended on convention: evidence completion accepted
client storage coordinates, and API/bridge RabbitMQ topology constants were
duplicated. The web console also displayed a green infrastructure status
without querying the dependencies.

## Decision

Evidence upload is a server-owned `EvidenceUploadIntent`. The browser receives
an opaque upload ID and presigned URL; completion resolves the intent, checks
the authoritative MinIO object, computes SHA-256, and stores the object version
ID, content type, and size. Report snapshots carry that complete byte identity.
Bridge and web/API screenshot adapters use the frozen version and checksum;
pre-version snapshots remain supported as an explicitly legacy compatibility
path.

RabbitMQ job topology is loaded by both deployables from
`packages/contracts/job_protocol.json`. Job messages require protocol version
1 and the bridge routes unsupported or malformed messages to the DLQ before
executing them.

The API exposes process liveness separately from dependency/readiness checks.
The dashboard consumes the real health state and renders healthy, degraded,
unavailable, or unknown; it never assumes an unobserved dependency is green.

## Consequences

Storage identity and report bytes are auditable and resistant to key
replacement. A client cannot redirect completion to another object. Protocol
changes have one topology source and an explicit version gate. Health checks
can add request overhead, so they are polled by the frontend and use bounded
timeouts. Existing historical report snapshots without a version ID retain a
legacy fallback and should be migrated only with an operationally approved
object-version policy.
