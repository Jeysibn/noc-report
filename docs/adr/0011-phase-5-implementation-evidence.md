# 0011 — Phase 5 implementation evidence and operating contract

- Status: implemented
- Date: 2026-09-14
- Supersedes: none; supplements ADR 0010

## Runtime truth

`SkillSnapshot.id` is the execution selector on API-created `Job`,
`AnalysisRun`, `Report`, and `ReportSnapshot` records. The active pointer is
read only while creating a new job. The bridge loads that ID from PostgreSQL,
verifies the captured SHA-256 bytes, recursively resolves the snapshot's
pinned dependency graph, and writes only those files into a per-job mount. It
never re-hashes or executes the mutable repository checkout for a
snapshot-backed job.

Snapshot registration may observe changed checkout files, but registration
creates a draft. Activation validates the manifest, input contract, schema,
renderer profile, and dependency references before switching the pointer.
Rollback activates an older immutable row. PostgreSQL also enforces one active
row per logical skill.

## Skill-owned behavior

The sandbox loads `SKILL.md`, `skill.yaml`, and `output.schema.json` from the
mounted snapshot. Claude receives that exact schema and the bridge performs a
second generic JSON Schema validation. Analysis field names are not declared
in the generic validator. Cache compatibility uses the evidence checksum,
snapshot content hash, requested model/effort, and the explicit preprocessor
and AI-policy axes; changing skill instructions, manifest, or schema changes
the snapshot hash and misses the cache naturally.

`execution_policy` is carried in the canonical job envelope and stored on the
analysis run as provenance. `input_contract_version`, preprocessor version,
policy version, schema hash, evidence hash, model, effort, telemetry, and
cache status are retained for audit and report assembly.

## Report seam

The DOCX adapter consumes `ReportDocument` only. Declarative report skills
may emit metadata plus headings, paragraphs, bilingual text, evidence,
screenshots, links, log references, analysis references, dividers, and page
breaks. Section order and meaning are upstream skill behavior. The active
daily-report skill now uses `renderer_profile: report-document-v1`; historical
snapshots retaining `daily_report_docx` continue through an isolated
compatibility assembler. Both use the same adapter. The bridge selects one
renderer profile and cannot fall through from the declarative path into the
legacy daily-report field mapping.

Frozen report snapshots retain the report snapshot ID and, for every embedded
analysis, its run ID, snapshot ID/hash/version, schema and input hashes,
policy, model/effort, cache/usage telemetry, and output hash.

## Delivery and leases

Outbox dispatch uses one row per transaction: select with `FOR UPDATE SKIP
LOCKED`, publish with confirmation, mark published, commit. A failure commits
only the failure metadata and leaves the event pending. RabbitMQ remains
at-least-once, so consumers retain their idempotent job claim behavior.

`OUTBOX_MODE=embedded` is the local/simple topology. `OUTBOX_MODE=external`
means the API does not start a dispatcher and an operator must run
`python -m app.outbox_worker` once for the deployment. The mode is validated
by application settings.

The bridge lease is the configured sandbox job timeout plus a safety margin,
and a separate database connection renews it during preprocessing, Claude,
and artifact upload. A duplicate delivery cannot reclaim a live job; a worker
crash leaves an expired lease that can be recovered.

## Acceptance coverage

The repository covers active-v1 → queued-job → active-v2 → v1 execution,
rollback, source mutation isolation, dynamic analysis schemas, generic report
layouts, report provenance, cache invalidation, outbox ownership, lease
renewal policy, protocol validation, recursive dependency materialization,
legacy-job provenance migration, permission hardening, and pinned CI
infrastructure. The live Claude end-to-end tests remain opt-in because they
consume the operator's subscription usage.

Migration `f1a2b3c4d5e6` backfills legacy Jobs by immutable hash or exact
registry label, fails if an executable historical Job cannot be mapped, and
then makes `jobs.skill_snapshot_id` non-null. New application-created jobs
therefore cannot enter the system without an immutable execution contract.

The legacy report adapter can be deleted after the retention window when no
Job, Report, or ReportSnapshot references a snapshot whose manifest declares
`daily_report_docx`, and no queued message carries that snapshot ID.
