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
available and ambiguous fields stay manual. This path is independent of the
external AI runtime boundary.

Run tests against a disposable real Postgres database. The fixture drops and
recreates its schema, so never point it at the live development database:

```bash
DATABASE_URL=postgresql+psycopg2://noc:noc@localhost:55432/noc_report_test \
TEST_DATABASE_URL=postgresql+psycopg2://noc:noc@localhost:55432/noc_report_test \
python3 -m pytest -q
```

Models use Postgres-only UUID types and evidence tests PUT/GET real objects,
so SQLite/mocked-S3 substitutes are not valid stand-ins.

### Hermes runtime

The API remains the application boundary. Set `AI_RUNTIME=hermes` only when
the separately deployed `ai-worker` and Hermes service are available; otherwise
the safe default is `AI_RUNTIME=disabled`. The API does not receive provider
credentials. The worker receives only its internal Hermes API key and the
application credentials needed to verify evidence and update job artifacts.

Start the local Hermes runtime from the repository root:

```bash
HERMES_API_KEY='replace-with-a-long-random-value' \
docker compose -f infrastructure/docker-compose.dev.yml up -d postgres minio rabbitmq hermes ai-worker
```

The Hermes API is internal-only in Compose. Provider login/model selection is a
manual Hermes setup step; do not commit provider credentials or copy them into
FastAPI configuration. The AI worker exposes loopback-only operator health on
port `8092`; set `AI_WORKER_HEALTH_URL` to the internal worker service URL when
the API itself runs in a container. Hermes exposes `/health` only on the
internal Docker network.

The worker has two isolated profiles:

- `noc-log-analysis` → `log-triage-summary` for incident log analysis.
- `noc-daily-report` → `daily-alert-report` for bilingual narrative only.

The Daily Alert Report route remains disabled unless the Phase 1 quality gate
has passed and the API is started with `DAILY_REPORT_AI_ENABLED=true`. The
application still freezes the ReportSnapshot and owns report section order,
evidence, screenshots, links, preview JSON, and DOCX rendering. Hermes only
supplies the validated semantic summary.
