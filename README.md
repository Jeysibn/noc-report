# NOC Report Builder

Monorepo for the NOC Report Builder system. See the master plan and Obsidian
vault (`03 Projects/NOC Report Builder/`) for full product and architecture
context.

## Layout

- `apps/web/` — React + TypeScript frontend (Vite, Tailwind, Vitest)
- `apps/api/` — FastAPI backend (auth/RBAC, incidents, evidence, OCR, jobs,
  reports, search, analytics, and administration)
- `bridge/noc_bridge/` — host-side Claude Code Bridge
- `skills/` — Claude Code skills (`log-triage-summary/`, `daily-alert-report/`)
- `sandbox/` — Docker sandbox for AI skill execution
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

The release checks are intentionally component-scoped because the API fixture
recreates its database and the bridge uses the same infrastructure:

```bash
npm run check:coherence
(cd apps/api && DATABASE_URL=postgresql+psycopg2://noc:noc@localhost:55432/noc_report_test \
  TEST_DATABASE_URL=postgresql+psycopg2://noc:noc@localhost:55432/noc_report_test \
  pytest tests/ -q)  # disposable DB only
PYTHONPATH=sandbox python3 -m pytest sandbox/tests/ -q
(cd bridge && PYTHONPATH=. pytest tests/ -q)
```

Run the API and bridge suites sequentially against separate test database
lifecycles; do not run them concurrently against the same local Postgres.

The API exposes `/health` for liveness and `/health/dependencies` plus
`/health/readiness` for real dependency state. The dashboard treats degraded,
unavailable, and unknown dependencies distinctly.

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
are not delegated to Claude. The DOCX and Web Preview continue to consume the
same `ReportDocument` semantic structure.

Cross-incident Findings is not part of the contract for newly generated
reports. Historical report artifacts and legacy renderer profiles remain
frozen: they are displayed and downloaded exactly as originally generated and
are not rewritten to match the current contract.

## Milestones

Implementation proceeds per the master plan's Development Milestones (§37).
See `CONTRIBUTING.md` for coding conventions and `docs/adr/` for accepted
architectural decisions.
