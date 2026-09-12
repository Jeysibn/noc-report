# Coding Conventions

## General

- Match the existing style in the file you're editing before introducing a new one.
- Prefer the simplest implementation that preserves the architectural boundaries
  defined in `docs/adr/` and the vault's Architecture doc — don't add abstraction
  that isn't earned yet by the current milestone's scope.
- Keep mock and real service adapters behind the same interface, so swapping a
  mock for a real backend integration never requires changing call sites.
- 2-space indentation for JS/TS/JSON/CSS/HTML, 4-space for Python (see `.editorconfig`).
- LF line endings, UTF-8, final newline on every file.

## Frontend (`apps/web`)

- TypeScript strict mode is on — no `any` without a comment explaining why.
- Function components + hooks only. No class components.
- Co-locate a component's test file next to it (`Thing.tsx` + `Thing.test.tsx`).
- Routing via `react-router-dom`; data fetching via TanStack Query once the API
  exists (Milestone 2+) — don't hand-roll fetch/state management in components.
- Styling via Tailwind utility classes; introduce a component class only for
  patterns repeated 3+ times.
- Run `npm run lint` and `npm run test` before committing.

## Backend (`apps/api`, added later)

- FastAPI route handlers stay thin; business logic lives in service modules.
- Pydantic models for all request/response schemas.
- SQLAlchemy 2.0 style (typed ORM), Alembic for all schema migrations.
- pytest + httpx for tests; no hitting a live DB in unit tests — use fixtures.

## Commits

- Small, milestone-scoped commits. Reference the milestone name in the message
  where relevant (e.g. `Milestone 0: scaffold frontend app shell`).

## Documentation

- Every milestone's completion is recorded in the Obsidian vault
  (`03 Projects/NOC Report Builder/02 Operations/Roadmap and Milestones.md`),
  not just in commit history.
