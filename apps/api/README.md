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

### Optional local OCR prefill

Start the bounded Ollama service only when local semantic mapping is desired:

```bash
docker compose -f ../../infrastructure/docker-compose.dev.yml --profile local-ai up -d ollama
docker exec noc-report-ollama ollama pull qwen2.5:3b-instruct-q4_K_M
```

Set `LOCAL_PREFILL_AI_ENABLED=true` and
`LOCAL_PREFILL_BASE_URL=http://localhost:11434`. Defaults are a small Q4 3B
instruct model, context 2048, five-minute keep-alive, one inference, a
measured local mapping timeout of 75 seconds, and a bounded queue. If Ollama
is unavailable, OCR and deterministic fields remain
available and ambiguous fields stay manual. This path never invokes Claude.

Run tests (against the same real Postgres + MinIO — models use
Postgres-only UUID types and evidence tests PUT/GET real objects, so
SQLite/mocked-S3 substitutes are not valid stand-ins):

```bash
python3 -m pytest -q
```

Tests drop and recreate the Postgres schema per test; after running the
suite, re-run the two commands above to restore a normal dev-mode
database.
