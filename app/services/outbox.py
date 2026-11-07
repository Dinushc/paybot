"""Transactional outbox: service events and Telegram notifications with retries."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import metrics
from app.config import settings
from app.i18n import t
from app.models import Account, OutboxEvent, Service, TelegramProfile, utcnow
from app.services.handoff import create_handoff

log = logging.getLogger("app.outbox")

NOTIFICATION = "notification.telegram"
PROCESSING_TIMEOUT_SECONDS = 300


def add_event(
    db: Session,
    *,
    event_type: str,
    aggregate_id: str | None,
    service_id: str | None,
    payload: dict,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        service_id=service_id,
        payload=payload,
        status="pending",
        next_attempt_at=utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def enqueue_user_notification(
    db: Session,
    *,
    account: Account,
    key: str,
    params: dict | None = None,
    include_open_service_id: str | None = None,
) -> OutboxEvent | None:
    profile = db.get(TelegramProfile, account.id)
    if profile is None:
        return None
    return add_event(
        db,
        event_type=NOTIFICATION,
        aggregate_id=account.id,
        service_id=include_open_service_id,
        payload={
            "chat_id": profile.telegram_user_id,
            "account_id": account.id,
            "lang": account.locale,
            "key": key,
            "params": params or {},
            "include_open_service_id": include_open_service_id,
        },
    )


def enqueue_admin_alert(
    db: Session, *, key: str, params: dict | None = None, chat_id: str | None = None
) -> OutboxEvent | None:
    target = chat_id or settings.admin_chat_id
    if not target:
        log.error("admin alert %s has no ADMIN_CHAT_ID: %s", key, params or {})
        return None
    return add_event(
        db,
        event_type=NOTIFICATION,
        aggregate_id=None,
        service_id=None,
        payload={
            "chat_id": target,
            "account_id": None,
            "lang": "en",
            "key": key,
            "params": params or {},
            "admin": True,
        },
    )


def message_for(db: Session, event: OutboxEvent) -> tuple[int | str, str, dict | None] | None:
    payload = event.payload or {}
    chat_id = payload.get("chat_id")
    if not chat_id:
        return None
    lang = payload.get("lang") or "en"
    key = str(payload.get("key") or "")
    if not key:
        return None
    params = dict(payload.get("params") or {})
    text = t(lang, key, **params)

    markup: dict | None = None
    service_id = payload.get("include_open_service_id")
    if service_id:
        service = db.get(Service, service_id)
        button_text = t(lang, "btn_open")
        if service is not None and service.status == "active":
            if service.webapp_url:
                markup = {"inline_keyboard": [[{"text": button_text, "web_app": {"url": service.webapp_url}}]]}
            elif service.handoff_url:
                account = db.get(Account, payload.get("account_id"))
                if account is not None:
                    handoff = create_handoff(db, account=account, service=service)
                    if handoff.get("url"):
                        markup = {
                            "inline_keyboard": [[{"text": button_text, "url": handoff["url"]}]]
                        }
    return chat_id, text, markup


def _backoff(attempts: int) -> timedelta:
    seconds = min(3600, 5 * (2 ** max(0, attempts - 1)))
    return timedelta(seconds=seconds)


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _deliver_service_event(db: Session, event: OutboxEvent) -> None:
    service = db.get(Service, event.service_id) if event.service_id else None
    if service is None or not service.events_url:
        raise _PermanentError("service has no events_url")
    envelope = {
        "id": event.id,
        "type": event.event_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": event.created_at.isoformat() if event.created_at else None,
        "data": event.payload or {},
    }
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Platform-Event": event.event_type,
        "X-Platform-Event-Id": event.id,
    }
    if service.events_secret:
        headers["X-Platform-Signature"] = _signature(service.events_secret, body)
    # Release the DB transaction before the network call.
    db.commit()
    try:
        response = httpx.post(service.events_url, content=body, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        raise _RetryableError("service endpoint unavailable") from exc
    if 200 <= response.status_code < 300:
        return
    if response.status_code in {408, 429} or response.status_code >= 500:
        raise _RetryableError(f"service replied {response.status_code}")
    raise _PermanentError(f"service replied {response.status_code}")


class _RetryableError(RuntimeError):
    pass


class _PermanentError(RuntimeError):
    pass


def _send_prepared(bot, prepared: tuple[int | str, str, dict | None]) -> None:
    from app.telegram import TelegramError

    chat_id, text, markup = prepared
    try:
        bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramError as exc:
        if exc.permanent:
            raise _PermanentError(str(exc)) from exc
        raise _RetryableError(str(exc)) from exc


def _prepare_notification(db: Session, event: OutboxEvent) -> tuple[int | str, str, dict | None]:
    prepared = message_for(db, event)
    if prepared is None:
        raise _PermanentError("notification payload is incomplete")
    return prepared


def dispatch_once(db: Session, bot, *, limit: int = 50) -> dict:
    """Claim due events, deliver them outside the claim transaction, then persist.

    Events are marked ``processing`` in a short transaction. Deliveries happen
    without any row lock; each result is committed separately. A crashed worker
    leaves ``processing`` rows that are reclaimed after ``PROCESSING_TIMEOUT``.
    """
    now = utcnow()
    db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.status == "processing",
            OutboxEvent.next_attempt_at < now,
        )
        .values(status="pending", next_attempt_at=now)
    )
    pending_count = db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "pending")
    ).scalar_one()
    metrics.gauge("outbox_pending", int(pending_count or 0))
    oldest = db.execute(
        select(func.min(OutboxEvent.created_at)).where(OutboxEvent.status == "pending")
    ).scalar_one()
    if oldest is not None:
        metrics.gauge("outbox_lag_seconds", max(0.0, (now - oldest).total_seconds()))

    due_query = (
        select(OutboxEvent.id)
        .where(OutboxEvent.status == "pending", OutboxEvent.next_attempt_at <= now)
        .order_by(OutboxEvent.next_attempt_at)
        .limit(limit)
    )
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect != "sqlite":
        due_query = due_query.with_for_update(skip_locked=True)
    due_ids = list(db.execute(due_query).scalars())
    if due_ids:
        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(due_ids), OutboxEvent.status == "pending")
            .values(
                status="processing",
                next_attempt_at=now + timedelta(seconds=PROCESSING_TIMEOUT_SECONDS),
            )
        )
    db.commit()

    stats = {"sent": 0, "retry": 0, "dead": 0}
    for event_id in due_ids:
        event = db.get(OutboxEvent, event_id)
        if event is None or event.status != "processing":
            continue
        try:
            if event.event_type == NOTIFICATION:
                prepared = _prepare_notification(db, event)
                db.commit()
                _send_prepared(bot, prepared)
            else:
                db.commit()
                _deliver_service_event(db, event)
        except _PermanentError as exc:
            event.status = "failed"
            event.last_error = str(exc)[:512]
            event.attempts += 1
            stats["dead"] += 1
            _notify_dead(db, event)
        except _RetryableError as exc:
            event.attempts += 1
            event.last_error = str(exc)[:512]
            if event.attempts >= settings.outbox_max_attempts:
                event.status = "dead"
                stats["dead"] += 1
                _notify_dead(db, event)
            else:
                event.status = "pending"
                event.next_attempt_at = utcnow() + _backoff(event.attempts)
                stats["retry"] += 1
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected outbox failure for %s", event.id)
            event.attempts += 1
            event.last_error = str(exc)[:512]
            if event.attempts >= settings.outbox_max_attempts:
                event.status = "dead"
                stats["dead"] += 1
                _notify_dead(db, event)
            else:
                event.status = "pending"
                event.next_attempt_at = utcnow() + _backoff(event.attempts)
                stats["retry"] += 1
        else:
            event.status = "sent"
            event.sent_at = utcnow()
            event.last_error = None
            stats["sent"] += 1
        db.commit()
    for key, value in stats.items():
        metrics.inc(f"outbox_{key}", value)
    return stats


def _notify_dead(db: Session, event: OutboxEvent) -> None:
    if event.event_type == NOTIFICATION:
        return
    enqueue_admin_alert(
        db,
        key="admin_outbox_dead",
        params={
            "event_id": event.id,
            "event_type": event.event_type,
            "attempts": event.attempts,
            "error": (event.last_error or "")[:200],
        },
    )
