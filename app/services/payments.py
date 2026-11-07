"""Telegram Stars checkout validation, payment recording and refunds."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import metrics
from app.config import settings
from app.i18n import t
from app.models import Account, Order, PaymentEvent, Service, ServiceProduct, utcnow
from app.security import mask
from app.services import audit, outbox
from app.services.accounts import get_by_telegram_id, get_or_create_telegram_account
from app.services.catalog import (
    product_available,
    service_name,
)
from app.services.entitlements import grant_for_order, revoke_unused_for_order
from app.services.errors import DomainError, NotFoundError
from app.services.orders import get_order
from app.telegram import TelegramError

log = logging.getLogger("app.payments")


def validate_pre_checkout(db: Session, query: dict) -> tuple[bool, str, str]:
    """Validate a pre_checkout_query against the database only.

    Returns ``(ok, error_text, lang)``. The caller answers Telegram from a
    worker thread; no external HTTP calls happen here.
    """
    payload = str(query.get("invoice_payload") or "")
    from_user = query.get("from") or {}
    telegram_user_id = from_user.get("id")
    order = get_order(db, payload) if payload else None
    account = get_by_telegram_id(db, telegram_user_id) if telegram_user_id else None
    lang = account.locale if account else "en"

    def fail(key: str) -> tuple[bool, str, str]:
        metrics.inc("pre_checkout_rejected", reason=key)
        return False, t(lang, key), lang

    if order is None:
        return fail("err_order_not_found")
    if account is None or order.account_id != account.id:
        return fail("err_order_not_found")
    if account.is_blocked or account.deleted_at is not None:
        return fail("err_user_blocked")
    if order.provider != "telegram_stars":
        return fail("err_order_not_payable")
    if order.status not in {"pending", "expired"}:
        return fail("err_order_not_payable")
    now = utcnow()
    if order.status == "expired":
        late_ok = (
            settings.allow_late_payment
            and order.expires_at is not None
            and order.expires_at + timedelta(minutes=settings.late_payment_max_minutes) >= now
        )
        if not late_ok:
            return fail("err_order_expired")
    if order.telegram_user_id is not None and int(order.telegram_user_id) != int(telegram_user_id):
        return fail("err_order_not_found")
    service = db.get(Service, order.service_id)
    product = db.get(ServiceProduct, order.product_id)
    if service is None or service.status != "active":
        return fail("err_service_inactive")
    if not product_available(service, product):
        return fail("err_product_inactive")
    if str(query.get("currency") or "") != "XTR":
        return fail("err_currency_mismatch")
    if int(query.get("total_amount") or 0) != int(order.price_stars):
        return fail("err_amount_mismatch")
    metrics.inc("pre_checkout_ok")
    return True, "", lang


def _record_event(
    db: Session,
    *,
    order: Order | None,
    service_id: str | None,
    charge: str,
    update_id: int | None,
    status: str,
    payload: dict,
) -> PaymentEvent:
    event = PaymentEvent(
        order_id=order.id if order else None,
        service_id=service_id,
        event_key=f"tg:{charge}" if charge else f"update:{update_id}",
        telegram_update_id=update_id,
        payment_id=charge or None,
        status=status,
        payload=payload,
    )
    db.add(event)
    db.flush()
    return event


def handle_successful_payment(
    db: Session, message: dict, update_id: int | None
) -> dict:
    """Record a successful Stars payment and grant access exactly once."""
    sp = message.get("successful_payment") or {}
    charge = str(sp.get("telegram_payment_charge_id") or "")
    payload = str(sp.get("invoice_payload") or "")
    currency = str(sp.get("currency") or "")
    amount = int(sp.get("total_amount") or 0)
    from_user = message.get("from") or {}
    account = get_or_create_telegram_account(db, from_user)
    lang = account.locale

    existing = db.execute(
        select(PaymentEvent).where(PaymentEvent.event_key == f"tg:{charge}")
    ).scalar_one_or_none()
    if existing is not None:
        metrics.inc("payments_duplicate")
        return {"status": "duplicate", "order_id": existing.order_id, "lang": lang}

    order = get_order(db, payload) if payload else None
    if order is None:
        _record_event(
            db,
            order=None,
            service_id=None,
            charge=charge,
            update_id=update_id,
            status="unknown_order",
            payload=sp,
        )
        outbox.enqueue_admin_alert(
            db,
            key="admin_unknown_order",
            params={"payload": payload[:80], "charge": mask(charge)},
        )
        outbox.enqueue_user_notification(db, account=account, key="payment_unknown")
        metrics.inc("payments_unknown_order")
        return {"status": "unknown", "order_id": None, "lang": lang}

    service = db.get(Service, order.service_id)
    product = db.get(ServiceProduct, order.product_id)
    if service is None or product is None:
        _record_event(
            db,
            order=order,
            service_id=order.service_id,
            charge=charge,
            update_id=update_id,
            status="broken_reference",
            payload=sp,
        )
        order.status = "review"
        order.review_reason = "broken_reference"
        outbox.enqueue_admin_alert(
            db,
            key="admin_payment_review",
            params={"order_id": order.id, "reason": "broken_reference"},
        )
        metrics.inc("payments_review", reason="broken_reference")
        return {"status": "review", "order_id": order.id, "lang": lang}

    def mismatch(reason: str) -> dict:
        _record_event(
            db,
            order=order,
            service_id=service.id,
            charge=charge,
            update_id=update_id,
            status=reason,
            payload=sp,
        )
        order.review_reason = reason
        if order.status not in {"paid", "refunded"}:
            order.status = "review"
        # Keep the charge on the order so support can still refund a reviewed
        # payment through Telegram.
        if not order.payment_id:
            order.payment_id = charge
        if order.telegram_user_id is None:
            order.telegram_user_id = int(from_user.get("id") or 0) or None
        outbox.enqueue_admin_alert(
            db,
            key="admin_amount_mismatch",
            params={
                "order_id": order.id,
                "expected": order.price_stars,
                "actual": amount,
                "charge": mask(charge),
                "reason": reason,
            },
        )
        outbox.enqueue_user_notification(db, account=account, key="payment_review")
        metrics.inc("payments_review", reason=reason)
        return {"status": "review", "order_id": order.id, "lang": lang}

    if account.is_blocked:
        return mismatch("user_blocked")
    if account.id != order.account_id:
        return mismatch("payer_mismatch")
    if order.telegram_user_id is not None and int(order.telegram_user_id) != int(from_user.get("id", 0)):
        return mismatch("payer_mismatch")
    if currency != "XTR":
        return mismatch("currency_mismatch")
    if amount != order.price_stars:
        return mismatch("amount_mismatch")
    if order.status in {"paid", "refunded", "review"}:
        if order.payment_id == charge:
            metrics.inc("payments_duplicate")
            return {"status": "duplicate", "order_id": order.id, "lang": lang}
        return mismatch("duplicate_payment")
    if order.status not in {"pending", "expired"}:
        return mismatch("invalid_status")
    if order.status == "expired":
        late_ok = (
            settings.allow_late_payment
            and order.expires_at is not None
            and order.expires_at + timedelta(minutes=settings.late_payment_max_minutes) >= utcnow()
        )
        if not late_ok:
            return mismatch("late_payment_expired")

    now = utcnow()
    order.status = "paid"
    order.paid_at = now
    order.payment_id = charge
    order.telegram_user_id = int(from_user.get("id") or order.telegram_user_id or 0) or None
    order.gross_stars = order.price_stars
    fee_bps = max(0, min(10000, int(service.platform_fee_bps or 0)))
    order.platform_fee_stars = order.price_stars * fee_bps // 10000
    order.service_share_stars = order.price_stars - order.platform_fee_stars
    order.review_reason = None

    _record_event(
        db,
        order=order,
        service_id=service.id,
        charge=charge,
        update_id=update_id,
        status="paid",
        payload=sp,
    )
    entitlement = grant_for_order(db, order=order, service=service, product=product)

    inactive = service.status != "active" or not product.active
    if inactive:
        order.status = "review"
        order.review_reason = "service_inactive" if service.status != "active" else "product_inactive"
        outbox.enqueue_admin_alert(
            db,
            key="admin_payment_review",
            params={"order_id": order.id, "reason": order.review_reason},
        )

    outbox.add_event(
        db,
        event_type="order.paid",
        aggregate_id=order.id,
        service_id=service.id,
        payload={
            "order_id": order.id,
            "account_id": order.account_id,
            "service_id": service.id,
            "service_code": service.code,
            "product_id": product.id,
            "product_code": product.code,
            "kind": entitlement.kind,
            "quantity": entitlement.quantity,
            "price_stars": order.price_stars,
            "status": order.status,
            "review_reason": order.review_reason,
        },
    )
    outbox.enqueue_user_notification(
        db,
        account=account,
        key="payment_review" if inactive else "payment_success",
        params={
            "service": service_name(service, lang),
            "quantity": entitlement.quantity,
        },
        include_open_service_id=service.id,
    )
    metrics.inc("payments_paid")
    log.info(
        "payment recorded",
        extra={
            "order_id": order.id,
            "account_id": account.id,
            "service_code": service.code,
            "charge_id": mask(charge),
            "status": order.status,
        },
    )
    return {
        "status": "review" if inactive else "paid",
        "order_id": order.id,
        "lang": lang,
        "service_id": service.id,
    }


def refund_order(
    db: Session, *, order_id: str, bot, actor: str, telegram_user_id: int | None = None
) -> dict:
    order = db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("order not found")
    if order.status == "refunded":
        raise DomainError("already_refunded", "order is already refunded", status_code=409)
    if order.status not in {"paid", "review"}:
        raise DomainError(
            "order_not_refundable", "only paid orders can be refunded", status_code=409
        )
    if order.provider != "telegram_stars" or not order.payment_id:
        raise DomainError(
            "order_not_refundable", "order has no Telegram Stars payment", status_code=409
        )
    payer = telegram_user_id or order.telegram_user_id
    if not payer:
        raise DomainError("missing_payer", "order has no Telegram payer", status_code=409)
    before = {
        "status": order.status,
        "payment_id": mask(order.payment_id),
    }
    try:
        bot.refund_star_payment(payer, order.payment_id)
    except TelegramError as exc:
        metrics.inc("refunds_failed")
        log.error("refund failed for order %s: %s", order.id, exc)
        raise DomainError(
            "telegram_refund_failed",
            "Telegram did not confirm the refund; the order stays paid",
            status_code=502,
        ) from exc

    order.status = "refunded"
    order.refunded_at = utcnow()
    order.review_reason = None
    revoked = revoke_unused_for_order(db, order.id)
    outbox.add_event(
        db,
        event_type="order.refunded",
        aggregate_id=order.id,
        service_id=order.service_id,
        payload={
            "order_id": order.id,
            "account_id": order.account_id,
            "service_id": order.service_id,
            "price_stars": order.price_stars,
            "revoked_entitlements": [item.id for item in revoked],
        },
    )
    account = db.get(Account, order.account_id)
    if account is not None:
        outbox.enqueue_user_notification(
            db, account=account, key="refund_done", params={"order_id": order.id}
        )
    audit.write_audit(
        db,
        actor=actor,
        action="order.refund",
        entity_type="order",
        entity_id=order.id,
        before=before,
        after={"status": "refunded", "revoked_entitlements": [item.id for item in revoked]},
    )
    metrics.inc("refunds_succeeded")
    log.info(
        "order refunded",
        extra={"order_id": order.id, "status": "refunded", "charge_id": mask(order.payment_id)},
    )
    return {
        "order_id": order.id,
        "status": order.status,
        "revoked_entitlements": [item.id for item in revoked],
    }
