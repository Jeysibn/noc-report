# NOC Report Builder — API

FastAPI backend for authentication and RBAC, shifts, incidents, audit,
evidence storage, OCR, queued jobs, analysis runs, shift reports, search,
analytics, and administration.

## Local setup

Dependencies are installed against system Python (no working `venv` in
this environment — `ensurepip` is unavailable and there's no sudo):

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt
```

Start PostgreSQL, MinIO, and RabbitMQ (using non-default host ports to avoid
colliding with other local projects):

```bash
docker compose -f ../../infrastructure/docker-compose.dev.yml up -d
```

Apply migrations and seed roles/permissions/an initial admin user
(buckets are created automatically on app startup, but a bare
`alembic upgrade head` + `app.seed` run is enough for scripts/tests
that don't boot the full app):

```bash
python3 -m alembic upgrade head
python3 -m app.seed   # admin / ChangeMe123! — change in any real deployment
```

Run the API:

```bash
python3 -m uvicorn app.main:app --reload --port 8000
```

Run tests (against the same real Postgres + MinIO — models use
Postgres-only UUID types and evidence tests PUT/GET real objects, so
SQLite/mocked-S3 substitutes are not valid stand-ins):

```bash
python3 -m pytest -q
```

Tests drop and recreate the Postgres schema per test; after running the
suite, re-run the two commands above to restore a normal dev-mode
database.
