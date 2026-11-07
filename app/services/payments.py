"""Telegram Stars checkout validation, payment recording and refunds."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import or_, select, update
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

REFUND_STALE_MINUTES = 10


def _late_payment_allowed(order: Order, now) -> bool:
    if order.expires_at is None:
        return True
    if order.expires_at >= now:
        return True
    return bool(
        settings.allow_late_payment
        and order.expires_at + timedelta(minutes=settings.late_payment_max_minutes) >= now
    )


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
    if not _late_payment_allowed(order, now):
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
    payer_telegram_id: int | None = None,
) -> PaymentEvent:
    event = PaymentEvent(
        order_id=order.id if order else None,
        service_id=service_id,
        event_key=f"tg:{charge}" if charge else f"update:{update_id}",
        telegram_update_id=update_id,
        payment_id=charge or None,
        payer_telegram_id=payer_telegram_id,
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
            payer_telegram_id=int(from_user.get("id") or 0) or None,
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
            payer_telegram_id=int(from_user.get("id") or 0) or None,
        )
        order.status = "review"
        order.review_reason = "broken_reference"
        if not order.payment_id:
            order.payment_id = charge
        if order.telegram_user_id is None:
            order.telegram_user_id = int(from_user.get("id") or 0) or None
        outbox.enqueue_admin_alert(
            db,
            key="admin_payment_review",
            params={"order_id": order.id, "reason": "broken_reference"},
        )
        outbox.enqueue_user_notification(db, account=account, key="payment_review")
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
            payer_telegram_id=int(from_user.get("id") or 0) or None,
        )
        order.review_reason = reason
        if order.status not in {"paid", "refunded"}:
            order.status = "review"
        # Attach the charge to the order only when the payer is the order owner;
        # payer-mismatch and duplicate charges stay on the payment event so
        # support can refund them to the actual payer.
        if reason not in {"payer_mismatch", "duplicate_payment"}:
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
    if not _late_payment_allowed(order, utcnow()):
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
        payer_telegram_id=int(from_user.get("id") or 0) or None,
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
    db: Session,
    *,
    order_id: str,
    bot,
    actor: str,
    telegram_user_id: int | None = None,
    reason: str | None = None,
) -> dict:
    order = db.get(Order, order_id)
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
    now = utcnow()
    stale_before = now - timedelta(minutes=REFUND_STALE_MINUTES)
    claim = db.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.status.in_(("paid", "review")),
            or_(
                Order.refund_started_at.is_(None),
                Order.refund_started_at < stale_before,
            ),
        )
        .values(refund_started_at=now)
    )
    if int(claim.rowcount or 0) != 1:
        db.rollback()
        raise DomainError("refund_in_progress", "a refund is already in progress", status_code=409)

    before = {
        "status": order.status,
        "payment_id": mask(order.payment_id),
        "reason": reason,
    }
    # The claim is committed before the network call, so no row lock is held.
    order.refund_started_at = now
    db.commit()

    try:
        bot.refund_star_payment(payer, order.payment_id)
    except TelegramError as exc:
        db.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(refund_started_at=None)
        )
        order.refund_started_at = None
        db.commit()
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
    # Mark the corresponding charge so it cannot be refunded a second time
    # through the payment-event endpoint.
    payment_event = db.execute(
        select(PaymentEvent).where(PaymentEvent.payment_id == order.payment_id)
    ).scalars().first()
    if payment_event is not None and payment_event.refunded_at is None:
        payment_event.refunded_at = order.refunded_at
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
        after={
            "status": "refunded",
            "revoked_entitlements": [item.id for item in revoked],
            "reason": reason,
        },
    )
    db.commit()
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


REFUNDABLE_EVENT_STATUSES = {
    "unknown_order",
    "amount_mismatch",
    "currency_mismatch",
    "payer_mismatch",
    "user_blocked",
    "broken_reference",
    "duplicate_payment",
    "late_payment_expired",
    "invalid_status",
}


def refund_payment_event(
    db: Session, *, event_id: str, bot, actor: str, reason: str | None = None
) -> dict:
    event = db.get(PaymentEvent, event_id)
    if event is None:
        raise NotFoundError("payment event not found")
    if event.refunded_at is not None:
        raise DomainError("already_refunded", "payment is already refunded", status_code=409)
    if not event.payment_id:
        raise DomainError("missing_payment", "payment event has no charge id", status_code=409)
    if event.status not in REFUNDABLE_EVENT_STATUSES:
        raise DomainError(
            "payment_not_refundable",
            "only review/duplicate charges can be refunded via the payment event",
            status_code=409,
        )
    order = db.get(Order, event.order_id) if event.order_id else None
    if (
        order is not None
        and order.payment_id == event.payment_id
        and order.status in {"paid", "review"}
    ):
        raise DomainError(
            "use_order_refund",
            "this charge is attached to an order; refund the order instead",
            status_code=409,
        )
    payer = event.payer_telegram_id or (order.telegram_user_id if order else None)
    if not payer:
        raise DomainError("missing_payer", "payment event has no payer", status_code=409)
    now = utcnow()
    stale_before = now - timedelta(minutes=REFUND_STALE_MINUTES)
    claim = db.execute(
        update(PaymentEvent)
        .where(
            PaymentEvent.id == event_id,
            PaymentEvent.refunded_at.is_(None),
            or_(
                PaymentEvent.refund_started_at.is_(None),
                PaymentEvent.refund_started_at < stale_before,
            ),
        )
        .values(refund_started_at=now)
    )
    if int(claim.rowcount or 0) != 1:
        db.rollback()
        raise DomainError("refund_in_progress", "a refund is already in progress", status_code=409)
    event.refund_started_at = now
    db.commit()

    try:
        bot.refund_star_payment(payer, event.payment_id)
    except TelegramError as exc:
        db.execute(
            update(PaymentEvent)
            .where(PaymentEvent.id == event_id)
            .values(refund_started_at=None)
        )
        event.refund_started_at = None
        db.commit()
        metrics.inc("refunds_failed")
        raise DomainError(
            "telegram_refund_failed",
            "Telegram did not confirm the refund",
            status_code=502,
        ) from exc

    event.refunded_at = utcnow()
    db.flush()
    audit.write_audit(
        db,
        actor=actor,
        action="payment_event.refund",
        entity_type="payment_event",
        entity_id=event.id,
        before={"payment_id": mask(event.payment_id), "status": event.status, "reason": reason},
        after={"refunded_at": event.refunded_at.isoformat(), "reason": reason},
    )
    db.commit()
    metrics.inc("refunds_succeeded")
    log.info(
        "payment event refunded",
        extra={
            "order_id": event.order_id,
            "status": "refunded",
            "charge_id": mask(event.payment_id),
        },
    )
    return {
        "event_id": event.id,
        "order_id": event.order_id,
        "payment_id": event.payment_id,
        "refunded_at": event.refunded_at.isoformat(),
    }
