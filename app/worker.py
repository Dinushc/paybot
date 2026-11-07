"""Background worker: outbox delivery, order/entitlement expiry, webhook health."""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import timedelta

from app import metrics
from app.config import settings
from app.db import session_scope
from app.services import outbox
from app.models import BotUpdate, InitDataUse, utcnow
from app.services.entitlements import expire_entitlements
from app.services.orders import expire_orders
from app.telegram import TelegramError, get_bot

log = logging.getLogger("app.worker")


class Worker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="paybot-worker", daemon=True)
        self._stop = threading.Event()
        self._last = {
            "outbox": 0.0,
            "orders": 0.0,
            "entitlements": 0.0,
            "webhook": 0.0,
            "cleanup": 0.0,
        }
        self._last_webhook_error: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("worker started")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - keep the worker alive
                log.exception("worker tick failed")
            self._stop.wait(1.0)
        log.info("worker stopped")

    def _due(self, name: str, interval: int) -> bool:
        now = time.monotonic()
        if now - self._last[name] >= interval:
            self._last[name] = now
            return True
        return False

    def tick(self) -> None:
        bot = get_bot()
        if self._due("outbox", settings.outbox_interval_seconds):
            with session_scope() as db:
                stats = outbox.dispatch_once(db, bot)
            if any(stats.values()):
                log.info("outbox tick: %s", stats)
        if self._due("orders", settings.order_expiry_interval_seconds):
            with session_scope() as db:
                expired = expire_orders(db)
            if expired:
                metrics.inc("orders_expired", expired)
                log.info("expired %s orders", expired)
        if self._due("entitlements", settings.entitlement_expiry_interval_seconds):
            with session_scope() as db:
                expired = expire_entitlements(db)
            if expired:
                metrics.inc("entitlements_expired", expired)
                log.info("expired %s entitlements", expired)
        if self._due("cleanup", 60 * 60):
            with session_scope() as db:
                removed = _cleanup_init_data(db)
            if removed:
                log.info("removed %s expired initData records", removed)
        if bot.configured and self._due("webhook", settings.webhook_check_interval_seconds):
            self._check_webhook(bot)

    def _check_webhook(self, bot) -> None:
        try:
            info = bot.get_webhook_info()
        except TelegramError as exc:
            log.warning("getWebhookInfo failed: %s", exc)
            return
        last_error = str(info.get("last_error_message") or "")
        pending = int(info.get("pending_update_count") or 0)
        metrics.gauge("telegram_pending_updates", pending)
        if last_error and last_error != self._last_webhook_error:
            log.error("Telegram webhook error: %s", last_error)
            with session_scope() as db:
                outbox.enqueue_admin_alert(
                    db, key="admin_webhook_error", params={"error": last_error[:200]}
                )
        self._last_webhook_error = last_error or None


def _cleanup_init_data(db) -> int:
    from sqlalchemy import delete, select

    removed = 0
    for _ in range(10):
        init_digests = list(
            db.execute(
                select(InitDataUse.digest)
                .where(InitDataUse.used_at < utcnow() - timedelta(days=1))
                .limit(1000)
            ).scalars()
        )
        update_ids = list(
            db.execute(
                select(BotUpdate.update_id)
                .where(BotUpdate.created_at < utcnow() - timedelta(days=30))
                .limit(1000)
            ).scalars()
        )
        if not init_digests and not update_ids:
            break
        if init_digests:
            result = db.execute(
                delete(InitDataUse).where(InitDataUse.digest.in_(init_digests))
            )
            removed += int(result.rowcount or 0)
        if update_ids:
            result = db.execute(
                delete(BotUpdate).where(BotUpdate.update_id.in_(update_ids))
            )
            removed += int(result.rowcount or 0)
        db.commit()
    return removed


def run_jobs_once() -> dict:
    """Run every recurring job once (used by the CLI and tests)."""
    result: dict[str, int] = {}
    with session_scope() as db:
        result["orders"] = expire_orders(db)
    with session_scope() as db:
        result["entitlements"] = expire_entitlements(db)
    with session_scope() as db:
        result["outbox"] = outbox.dispatch_once(db, get_bot())["sent"]
    return result


def main() -> None:
    from app.log import setup_logging

    setup_logging()
    worker = Worker()
    stop = threading.Event()

    def _handle(signum, _frame) -> None:
        log.info("signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    worker.start()
    while not stop.is_set():
        stop.wait(1.0)
    worker.stop()
    worker.join(timeout=10)


if __name__ == "__main__":
    main()
