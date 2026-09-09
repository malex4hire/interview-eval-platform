"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import get_settings
from app.api.routers import audit, auth, candidate, interviews

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Backend and public API for AI-evaluated technical interviews. "
        "Every evaluation is scored by a pluggable provider, gated on a "
        "confidence threshold, and written to an append-only SHA-256 "
        "hash-chained audit log."
    ),
)

app.include_router(auth.router)
app.include_router(interviews.router)
app.include_router(candidate.router)
app.include_router(audit.router)


@app.get("/health", tags=["ops"])
def health() -> dict[str, object]:
    """Liveness plus the two settings that change how verdicts are produced.

    Exposing them here means an operator can confirm which threshold and which
    provider produced a given day's results without reading the deployment.
    """
    return {
        "status": "ok",
        "environment": settings.environment,
        "confidence_threshold": settings.confidence_threshold,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model_name,
    }
