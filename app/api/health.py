"""Health, readiness and metrics endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app import metrics
from app.config import settings
from app.db import database_ready

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz():
    ready = database_ready()
    return PlainTextResponse(
        "ready" if ready else "not ready", status_code=200 if ready else 503
    )


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    if not settings.metrics_enabled:
        return PlainTextResponse("metrics disabled\n", status_code=404)
    return PlainTextResponse(metrics.render())
