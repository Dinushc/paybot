"""Telegram webhook endpoint."""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import metrics
from app.bot.gateway import handle_update
from app.config import settings
from app.ratelimit import SlidingWindowLimiter
from app.security import verify_webhook_secret
from app.services.errors import DomainError

log = logging.getLogger("app.webhook")

router = APIRouter()
_limiter = SlidingWindowLimiter(settings.webhook_rate_limit_per_minute)


def _client_ip(request: Request) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/api/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not verify_webhook_secret(x_telegram_bot_api_secret_token):
        metrics.inc("webhook_rejected", reason="secret")
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    if not _limiter.allow(_client_ip(request)):
        metrics.inc("webhook_rejected", reason="rate_limit")
        return JSONResponse({"ok": False, "error": "rate limited"}, status_code=429)

    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.webhook_max_body_bytes:
        metrics.inc("webhook_rejected", reason="too_large")
        return JSONResponse({"ok": False, "error": "payload too large"}, status_code=413)
    body = await request.body()
    if len(body) > settings.webhook_max_body_bytes:
        metrics.inc("webhook_rejected", reason="too_large")
        return JSONResponse({"ok": False, "error": "payload too large"}, status_code=413)
    try:
        update = json.loads(body)
    except ValueError:
        metrics.inc("webhook_rejected", reason="invalid_json")
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(update, dict):
        metrics.inc("webhook_rejected", reason="invalid_json")
        return JSONResponse({"ok": False, "error": "invalid update"}, status_code=400)

    update_id = update.get("update_id")
    metrics.inc("updates_received")
    started = time.perf_counter()
    try:
        await run_in_threadpool(handle_update, update)
    except DomainError as exc:
        # Business validation errors are acknowledged; retrying cannot help.
        metrics.inc("updates_failed", reason=exc.code)
        log.warning("update %s rejected: %s", update_id, exc.code)
        return JSONResponse({"ok": True, "status": "ignored"})
    except Exception as exc:
        metrics.inc("updates_failed", reason="exception")
        log.exception("update %s failed", update_id)
        raise HTTPException(status_code=500, detail="retryable error") from exc
    finally:
        metrics.gauge("webhook_latency_seconds", time.perf_counter() - started)
    metrics.inc("updates_processed")
    return {"ok": True}
