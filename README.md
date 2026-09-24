# NOC Report Builder

Monorepo for the NOC Report Builder system. See the master plan and Obsidian
vault (`03 Projects/NOC Report Builder/`) for full product and architecture
context.

## Layout

- `apps/web/` — React + TypeScript frontend (Vite, Tailwind, Vitest)
- `apps/api/` — FastAPI backend (auth/RBAC, incidents, evidence, OCR, jobs,
  reports, search, analytics, and administration)
- `bridge/noc_bridge/` — provider-neutral queue/storage helpers plus the thin Hermes AI Worker
- `skills/` — provider-neutral NOC skills (`log-triage-summary/`, `daily-alert-report/`)
- `sandbox/` — deterministic log preprocessing utilities
- `packages/contracts/` — shared schemas/types used by frontend and backend
- `infrastructure/` — docker/caddy/rabbitmq/minio/postgres/systemd/backup configs
- `docs/adr/` — architecture decision records
- `docs/ui/` — UI specs, including the UI Product Freeze gate doc
- `docs/production-hardening.md` — evidence identity, job protocol, health,
  deployment, and verification runbook

## Development

The frontend is an npm workspace:

```bash
npm install
npm run dev      # start the frontend dev server
npm run test     # run frontend tests
npm run build    # build the frontend
npm run lint     # lint the frontend
npm run typecheck
npm run format   # format the whole repo with Prettier
```

The backend has its own Python dependencies and requires PostgreSQL, MinIO,
and RabbitMQ. See [`apps/api/README.md`](apps/api/README.md) for its setup and
test commands. The development Compose file starts those dependencies; the web
app and API run as host processes.

Optional local OCR semantic mapping is feature-flagged and uses PaddleOCR for
text extraction, deterministic rules first, and Ollama only for unresolved
field mapping. Suggestions require operator review before an Incident is
created. See [`docs/phase-12-local-ai.md`](docs/phase-12-local-ai.md).

The semantic runtime is provider-neutral. Local API development defaults to
`AI_RUNTIME=disabled`; the Compose stack enables Hermes and the independent
log worker. Daily Alert Report processing has a separate opt-in worker and
queue consumer. The workers own evidence verification, deterministic
preprocessing, RabbitMQ settlement, deterministic report rendering, and result
validation; Hermes owns model/provider execution. Historical analyses and
reports remain readable regardless of runtime availability.

```bash
HERMES_API_KEY='replace-with-a-long-random-value' \
docker compose -f infrastructure/docker-compose.dev.yml up -d postgres minio rabbitmq hermes ai-worker
```

Daily Report is disabled by default. When enabling it, use the same gate value
for the API and Compose so Hermes provisions the optional profile; then start
the batch worker:

```bash
DAILY_REPORT_AI_ENABLED=true HERMES_API_KEY='replace-with-a-long-random-value' \
  docker compose -f infrastructure/docker-compose.dev.yml --profile daily-report up -d hermes daily-report-worker
```

The shared Hermes gateway always requires the `noc-log-analysis` profile. It
only provisions `noc-daily-report` when `DAILY_REPORT_AI_ENABLED=true`. A
Daily profile provisioning error is logged and leaves the Daily worker
not-ready without preventing the gateway from serving log analysis. The API
and Compose must receive the same feature-gate value.

`HERMES_API_KEY` is required; Compose intentionally fails closed rather than
using a shared development credential. For production, set `ENVIRONMENT=production`
for the API and `RUNTIME_ENVIRONMENT=production` for workers. Both accept only
`development`, `test`, or `production`; production startup rejects development
credentials and non-HTTPS browser origins.

Configure the provider inside the dedicated Hermes profiles before submitting a
real analysis or report. Set `DAILY_REPORT_AI_ENABLED=true` on the API only
after the log-analysis quality gate has passed. See
[`docs/architecture/provider-neutral-ai-runtime.md`](docs/architecture/provider-neutral-ai-runtime.md),
[`docs/adr/0029-hermes-runtime-integration.md`](docs/adr/0029-hermes-runtime-integration.md),
[`docs/adr/0030-hermes-daily-alert-report.md`](docs/adr/0030-hermes-daily-alert-report.md),
and [`docs/runbooks/hermes-log-analysis.md`](docs/runbooks/hermes-log-analysis.md).

The release checks are intentionally component-scoped because the API fixture
recreates its database and the runtime-support tests use the same infrastructure:

```bash
npm run check:coherence
(cd apps/api && DATABASE_URL=postgresql+psycopg2://noc:noc@localhost:55432/noc_report_test \
  TEST_DATABASE_URL=postgresql+psycopg2://noc:noc@localhost:55432/noc_report_test \
  pytest tests/ -q)  # disposable DB only
PYTHONPATH=sandbox python3 -m pytest sandbox/tests/ -q
(cd bridge && PYTHONPATH=. pytest tests/ -q)
```

Run the API and runtime-support suites sequentially against separate test database
lifecycles; do not run them concurrently against the same local Postgres.

The API exposes `/health` for liveness and `/health/dependencies` plus
`/health/readiness` for dependency state. Workers expose `/health/live` and
`/health/ready`; container health uses readiness. The Admin Runtime tab shows
worker/profile state, queue and DLQ depths, and disabled features without
exposing credentials.

## Daily Alert Report contract

New Daily Alert Reports are composed deterministically from the frozen report
snapshot and use this canonical main-section order:

```text
Alerts
  - Alert Navigation
  - compact alert evidence screenshots
General Summary
Log Analysis
  - one deterministic bookmark target per eligible alert
```

The Alerts navigation entries and eligible evidence headings link to their
corresponding Log Analysis bookmark in the generated DOCX. Screenshot sizing,
pair layout, section order, bookmarks, and hyperlinks are renderer-owned; they
are not delegated to an AI runtime. The DOCX and Web Preview continue to consume the
same `ReportDocument` semantic structure.

Cross-incident Findings is not part of the contract for newly generated
reports. Historical report artifacts and legacy renderer profiles remain
frozen: they are displayed and downloaded exactly as originally generated and
are not rewritten to match the current contract.

RabbitMQ redelivery and the runtime support package's PostgreSQL Job lease are deliberately
settled together: a live lease sends a duplicate through the delayed retry
queue, an expired lease is reclaimable, and a completed duplicate is ACKed
without another paid-AI execution. The Dashboard and Log Analysis UI also
distinguish failed remote reads from truthful empty states. Published outbox
rows are retained for the configured window and cleaned in bounded batches;
unpublished rows are never deleted by retention.

## Milestones

Implementation proceeds per the master plan's Development Milestones (§37).
See `CONTRIBUTING.md` for coding conventions and `docs/adr/` for accepted
architectural decisions.
