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

## Development

The frontend is an npm workspace:

```bash
npm install
npm run dev      # start the frontend dev server
npm run test     # run frontend tests
npm run build    # build the frontend
npm run lint     # lint the frontend
npm run format   # format the whole repo with Prettier
```

The backend has its own Python dependencies and requires PostgreSQL, MinIO,
and RabbitMQ. See [`apps/api/README.md`](apps/api/README.md) for its setup and
test commands. The development Compose file starts those dependencies; the web
app and API run as host processes.

## Milestones

Implementation proceeds per the master plan's Development Milestones (§37).
See `CONTRIBUTING.md` for coding conventions and `docs/adr/` for accepted
architectural decisions.
