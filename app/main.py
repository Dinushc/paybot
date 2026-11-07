"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import metrics
from app.api import admin as admin_api
from app.api import health, internal, webhook
from app.api.admin_ui import router as admin_ui_router
from app.config import settings
from app.db import init_db, session_scope
from app.log import setup_logging
from app.services.admin_access import ensure_bootstrap_admin
from app.services.errors import DomainError
from app.telegram import get_bot
from app.worker import Worker

log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    problems = settings.fatal_problems()
    if problems:
        raise RuntimeError("unsafe configuration: " + "; ".join(problems))
    for warning in settings.warnings():
        log.warning("config: %s", warning)

    if settings.auto_create_tables:
        init_db()
    with session_scope() as db:
        ensure_bootstrap_admin(db)

    if settings.telegram_enabled and settings.telegram_auto_setup:
        from app.bot.gateway import register_commands, setup_webhook

        bot = get_bot()
        try:
            setup_webhook(bot)
            log.info("webhook registered at %s", settings.webhook_url)
        except Exception as exc:  # pragma: no cover - network dependent
            log.error("setWebhook failed: %s", exc)
        try:
            register_commands(bot)
        except Exception as exc:  # pragma: no cover - network dependent
            log.error("registering bot commands failed: %s", exc)

    worker: Worker | None = None
    if settings.workers_enabled:
        worker = Worker()
        worker.start()

    yield

    if worker is not None:
        worker.stop()
        worker.join(timeout=10)


def create_app() -> FastAPI:
    app = FastAPI(
        title="PayBot platform",
        version="0.1.0",
        description="Telegram Stars platform for selling digital services",
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Admin-CSRF"],
        )

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError):
        if exc.status_code >= 500:
            metrics.inc("domain_errors", code=exc.code)
        return JSONResponse(exc.as_dict(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        errors = []
        for item in exc.errors():
            errors.append(
                {"loc": list(item.get("loc", [])), "msg": item.get("msg", ""), "type": item.get("type", "")}
            )
        return JSONResponse(
            {"error": {"code": "validation_error", "message": "invalid request", "details": errors}},
            status_code=422,
        )

    app.include_router(webhook.router)
    app.include_router(internal.router)
    app.include_router(admin_api.router)
    app.include_router(admin_ui_router)
    app.include_router(health.router)

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "name": "paybot",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/healthz",
        }

    return app


app = create_app()
