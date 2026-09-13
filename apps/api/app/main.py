from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import admin, analysis, analytics, auth, evidence, incidents, ocr, reports, search, shifts
from app.core.config import assert_production_secrets_are_safe, settings
from app.core.storage import ensure_buckets
from app.outbox_worker import start_background_thread


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Reliability mission Phase 17: refuse to start at all rather than
    # silently serve a production deployment on dev-only credentials.
    assert_production_secrets_are_safe()
    # Idempotent — mirrors app/seed.py's bootstrap-on-start approach.
    ensure_buckets()
    # Reliability mission Batch A / Skill Runtime mission Phase 12:
    # dev-convenience in-process outbox dispatcher (see
    # app/outbox_worker.py's docstring), now gated behind an explicit
    # `outbox_mode` setting instead of always running. "embedded" (the
    # default, preserving today's zero-config local-dev behavior) runs it
    # in a background thread here; "external" leaves dispatching entirely
    # to a standalone `python -m app.outbox_worker` process, so a real
    # deployment can run exactly one dispatcher without editing code. Runs
    # in a background thread and reconnects on its own; a RabbitMQ outage
    # at startup never blocks the API from serving requests.
    stop_event = None
    if settings.outbox_mode == "embedded":
        _thread, stop_event = start_background_thread()
    yield
    if stop_event is not None:
        stop_event.set()


app = FastAPI(title="NOC Report Builder API", version="0.1.0", lifespan=lifespan)

# Milestone 8 scope: allow the local Vite dev server only. Revisit once a
# real deployment target (and its origin) exists.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(shifts.router, prefix="/api/v1")
app.include_router(incidents.router, prefix="/api/v1")
app.include_router(evidence.router, prefix="/api/v1")
app.include_router(ocr.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
