"""Entitlement granting, consuming, expiry and revocation."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Entitlement,
    EntitlementLedger,
    Order,
    Service,
    ServiceProduct,
    utcnow,
)
from app.services.errors import DomainError, NotFoundError, ValidationError


def grant_for_order(
    db: Session, *, order: Order, service: Service, product: ServiceProduct
) -> Entitlement:
    """Grant the product entitlement exactly once per order and kind."""
    existing = db.execute(
        select(Entitlement).where(
            Entitlement.source_order_id == order.id,
            Entitlement.kind == product.entitlement_kind,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    now = utcnow()
    expires_at = None
    if product.entitlement_ttl_days:
        expires_at = now + timedelta(days=product.entitlement_ttl_days)
    entitlement = Entitlement(
        account_id=order.account_id,
        service_id=service.id,
        kind=product.entitlement_kind,
        quantity=product.entitlement_quantity,
        remaining=product.entitlement_quantity,
        status="active",
        source_order_id=order.id,
        expires_at=expires_at,
    )
    db.add(entitlement)
    db.flush()
    db.add(
        EntitlementLedger(
            entitlement_id=entitlement.id,
            account_id=entitlement.account_id,
            service_id=entitlement.service_id,
            delta=product.entitlement_quantity,
            reason="purchase",
            event_metadata={"order_id": order.id},
        )
    )
    db.flush()
    return entitlement


def _expire_stale(
    db: Session, *, account_id: str, service_id: str, kind: str | None = None
) -> int:
    now = utcnow()
    query = select(Entitlement).where(
        Entitlement.account_id == account_id,
        Entitlement.service_id == service_id,
        Entitlement.status == "active",
        Entitlement.expires_at.is_not(None),
        Entitlement.expires_at <= now,
    )
    if kind:
        query = query.where(Entitlement.kind == kind)
    expired = 0
    for entitlement in db.execute(query.with_for_update()).scalars():
        if entitlement.remaining > 0:
            db.add(
                EntitlementLedger(
                    entitlement_id=entitlement.id,
                    account_id=entitlement.account_id,
                    service_id=entitlement.service_id,
                    delta=-entitlement.remaining,
                    reason="expiry",
                    event_metadata={"kind": entitlement.kind},
                )
            )
        entitlement.remaining = 0
        entitlement.status = "expired"
        expired += 1
    if expired:
        db.flush()
    return expired


def get_balance(
    db: Session,
    *,
    account_id: str,
    service_id: str,
    kind: str | None = None,
) -> dict:
    _expire_stale(db, account_id=account_id, service_id=service_id, kind=kind)
    query = select(Entitlement).where(
        Entitlement.account_id == account_id,
        Entitlement.service_id == service_id,
        Entitlement.status == "active",
    )
    if kind:
        query = query.where(Entitlement.kind == kind)
    entitlements = list(db.execute(query.order_by(Entitlement.created_at)).scalars())
    total = sum(item.remaining for item in entitlements)
    return {
        "balance": total,
        "items": [
            {
                "entitlement_id": item.id,
                "kind": item.kind,
                "remaining": item.remaining,
                "quantity": item.quantity,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "status": item.status,
            }
            for item in entitlements
        ],
    }


def consume(
    db: Session,
    *,
    service_id: str,
    account_id: str,
    kind: str,
    quantity: int,
    idempotency_key: str,
    event_metadata: dict | None = None,
) -> dict:
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header is required")
    if quantity < 1:
        raise ValidationError("quantity must be at least 1")
    if not kind.strip():
        raise ValidationError("kind is required")

    request = {"account_id": account_id, "kind": kind, "quantity": quantity}
    replay = db.execute(
        select(EntitlementLedger).where(
            EntitlementLedger.service_id == service_id,
            EntitlementLedger.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if replay is not None:
        meta = replay.event_metadata or {}
        if meta.get("request") and meta["request"] != request:
            raise DomainError(
                "idempotency_conflict",
                "idempotency key was already used for a different request",
                status_code=409,
            )
        if meta.get("response"):
            return {**meta["response"], "replayed": True}
        # Legacy/edge case: recompute the response without consuming again.
        balance = get_balance(db, account_id=account_id, service_id=service_id, kind=kind)
        return {
            "ok": True,
            "entitlement_id": replay.entitlement_id,
            "remaining": balance["balance"],
            "balance": balance["balance"],
            "replayed": True,
        }

    _expire_stale(db, account_id=account_id, service_id=service_id, kind=kind)

    query = (
        select(Entitlement)
        .where(
            Entitlement.account_id == account_id,
            Entitlement.service_id == service_id,
            Entitlement.kind == kind,
            Entitlement.status == "active",
        )
        .order_by(
            case((Entitlement.expires_at.is_(None), 1), else_=0),
            Entitlement.expires_at,
            Entitlement.created_at,
        )
        .with_for_update()
    )
    entitlements = list(db.execute(query).scalars())
    available = sum(item.remaining for item in entitlements)
    if available < quantity:
        other_service = db.execute(
            select(func.count(Entitlement.id)).where(
                Entitlement.account_id == account_id,
                Entitlement.service_id != service_id,
                Entitlement.kind == kind,
                Entitlement.status == "active",
                Entitlement.remaining > 0,
            )
        ).scalar_one()
        if other_service:
            raise DomainError(
                "service_mismatch",
                "the entitlement belongs to another service",
                status_code=409,
            )
        expired_exists = db.execute(
            select(func.count(Entitlement.id)).where(
                Entitlement.account_id == account_id,
                Entitlement.service_id == service_id,
                Entitlement.kind == kind,
                Entitlement.status.in_(("expired", "revoked")),
            )
        ).scalar_one()
        if expired_exists and available == 0:
            raise DomainError("expired", "entitlement expired", status_code=409)
        raise DomainError(
            "insufficient_entitlement",
            "not enough access remaining",
            status_code=402,
            details={"available": available},
        )

    needed = quantity
    first_entitlement: Entitlement | None = None
    first_ledger: EntitlementLedger | None = None
    consumed: list[dict] = []
    for entitlement in entitlements:
        if needed <= 0:
            break
        take = min(entitlement.remaining, needed)
        entitlement.remaining -= take
        if entitlement.remaining == 0:
            entitlement.status = "exhausted"
        ledger_meta = {"request": request, "metadata": event_metadata or {}}
        ledger = EntitlementLedger(
            entitlement_id=entitlement.id,
            account_id=entitlement.account_id,
            service_id=entitlement.service_id,
            delta=-take,
            reason="consume",
            idempotency_key=idempotency_key if first_ledger is None else None,
            event_metadata=ledger_meta,
        )
        db.add(ledger)
        if first_ledger is None:
            first_ledger = ledger
            first_entitlement = entitlement
        consumed.append({"entitlement_id": entitlement.id, "quantity": take})
        needed -= take
    db.flush()

    balance = get_balance(db, account_id=account_id, service_id=service_id, kind=kind)
    response = {
        "ok": True,
        "entitlement_id": first_entitlement.id if first_entitlement else "",
        "remaining": balance["balance"],
        "balance": balance["balance"],
        "consumed": consumed,
    }
    if first_ledger is not None:
        meta = dict(first_ledger.event_metadata or {})
        meta["response"] = response
        first_ledger.event_metadata = meta
        db.flush()
    return response


def expire_entitlements(db: Session) -> int:
    from app.services import outbox as outbox_service
    from app.services.catalog import service_name

    now = utcnow()
    entitlements = list(
        db.execute(
            select(Entitlement).where(
                Entitlement.status == "active",
                Entitlement.expires_at.is_not(None),
                Entitlement.expires_at <= now,
            )
        ).scalars()
    )
    for entitlement in entitlements:
        if entitlement.remaining > 0:
            db.add(
                EntitlementLedger(
                    entitlement_id=entitlement.id,
                    account_id=entitlement.account_id,
                    service_id=entitlement.service_id,
                    delta=-entitlement.remaining,
                    reason="expiry",
                    event_metadata={"kind": entitlement.kind},
                )
            )
        entitlement.remaining = 0
        entitlement.status = "expired"
        account = db.get(Account, entitlement.account_id)
        service = db.get(Service, entitlement.service_id)
        if account is not None and service is not None:
            outbox_service.enqueue_user_notification(
                db,
                account=account,
                key="access_expired",
                params={"service": service_name(service, account.locale)},
            )
    return len(entitlements)


def revoke_unused_for_order(db: Session, order_id: str) -> list[Entitlement]:
    """Revoke only the unused remainder of an order's entitlements.

    Access that was already consumed is not clawed back automatically; that is
    an explicit admin decision instead.
    """
    entitlements = list(
        db.execute(
            select(Entitlement)
            .where(Entitlement.source_order_id == order_id)
            .with_for_update()
        ).scalars()
    )
    revoked: list[Entitlement] = []
    for entitlement in entitlements:
        if entitlement.remaining <= 0:
            continue
        db.add(
            EntitlementLedger(
                entitlement_id=entitlement.id,
                account_id=entitlement.account_id,
                service_id=entitlement.service_id,
                delta=-entitlement.remaining,
                reason="refund",
                event_metadata={"order_id": order_id},
            )
        )
        entitlement.remaining = 0
        entitlement.status = "revoked"
        revoked.append(entitlement)
    db.flush()
    return revoked


def adjust(
    db: Session,
    *,
    entitlement_id: str,
    delta: int,
    actor: str,
    reason: str = "adjust",
) -> Entitlement:
    entitlement = db.get(Entitlement, entitlement_id)
    if entitlement is None:
        raise NotFoundError("entitlement not found")
    if delta == 0:
        raise ValidationError("delta must not be zero")
    if entitlement.status == "revoked":
        raise DomainError("entitlement_revoked", "revoked entitlement cannot be adjusted")
    new_remaining = entitlement.remaining + delta
    if new_remaining < 0:
        raise ValidationError("adjustment would make remaining negative")
    entitlement.remaining = new_remaining
    if new_remaining == 0:
        entitlement.status = "exhausted"
    elif entitlement.status in {"exhausted", "expired"}:
        entitlement.status = "active"
    db.add(
        EntitlementLedger(
            entitlement_id=entitlement.id,
            account_id=entitlement.account_id,
            service_id=entitlement.service_id,
            delta=delta,
            reason=reason,
            event_metadata={"actor": actor},
        )
    )
    db.flush()
    return entitlement


def balance_by_service(db: Session, account: Account) -> list[dict]:
    from app.services.catalog import active_services

    result = []
    for service in active_services(db):
        result.append(
            {
                "service": service,
                "balance": get_balance(
                    db, account_id=account.id, service_id=service.id
                )["balance"],
            }
        )
    return result
