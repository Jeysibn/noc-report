from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import admin, analysis, analytics, auth, evidence, incidents, ocr, reports, search, shifts
from app.core.storage import ensure_buckets


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Idempotent — mirrors app/seed.py's bootstrap-on-start approach.
    ensure_buckets()
    yield


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
