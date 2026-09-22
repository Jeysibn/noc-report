"""Provider-neutral boundary for optional semantic analysis.

FastAPI never imports Hermes or a provider SDK. It only gates creation of
durable analysis jobs; the separately deployed AI Worker owns the runtime
call and its credentials.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException, status


AI_RUNTIME_UNAVAILABLE_CODE = "AI_RUNTIME_UNAVAILABLE"
AI_RUNTIME_UNAVAILABLE_MESSAGE = "The analysis engine is not currently configured."


class AIRuntime(Protocol):
    def analyze(self, job_input: dict) -> dict:
        """Return provider-neutral semantic analysis for one job input."""


def require_ai_runtime() -> None:
    """Reject new work unless the application-side runtime switch is enabled."""
    from app.core.config import settings

    if settings.ai_runtime == "hermes":
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": AI_RUNTIME_UNAVAILABLE_CODE,
            "message": AI_RUNTIME_UNAVAILABLE_MESSAGE,
        },
    )
