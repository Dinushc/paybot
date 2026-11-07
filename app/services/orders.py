"""Order creation, deduplication and expiry."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, Order, Service, ServiceProduct, utcnow
from app.services.errors import DomainError


def create_or_reuse_order(
    db: Session,
    *,
    account: Account,
    service: Service,
    product: ServiceProduct,
    telegram_user_id: int | None,
) -> tuple[Order, bool]:
    """Create a pending Stars order or reuse a very recent pending one.

    Repeated clicks inside ``ORDER_DEDUPE_SECONDS`` return the same invoice.
    The price is frozen on the order and never recalculated.
    """
    if service.status != "active":
        raise DomainError("service_unavailable", "service is not active", status_code=409)
    if not product.active or product.service_id != service.id:
        raise DomainError("product_unavailable", "product is not active", status_code=409)
    if product.price_stars < 1:
        raise DomainError("invalid_price", "product price must be at least 1 Star")

    now = utcnow()
    cutoff = now - timedelta(seconds=settings.order_dedupe_seconds)
    existing = db.execute(
        select(Order)
        .where(
            Order.account_id == account.id,
            Order.product_id == product.id,
            Order.status == "pending",
            Order.created_at >= cutoff,
        )
        .order_by(Order.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    order = Order(
        account_id=account.id,
        service_id=service.id,
        product_id=product.id,
        provider="telegram_stars",
        status="pending",
        price_stars=product.price_stars,
        telegram_user_id=telegram_user_id,
        invoice_payload=None,
        expires_at=now + timedelta(minutes=settings.order_ttl_minutes),
    )
    db.add(order)
    db.flush()
    order.invoice_payload = order.id
    db.flush()
    return order, True


def get_order(db: Session, order_id: str) -> Order | None:
    return db.get(Order, order_id)


def list_orders(
    db: Session, account_id: str, *, limit: int = 10
) -> list[Order]:
    return list(
        db.execute(
            select(Order)
            .where(Order.account_id == account_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def expire_orders(db: Session) -> int:
    now = utcnow()
    result = db.execute(
        update(Order)
        .where(Order.status == "pending", Order.expires_at.is_not(None), Order.expires_at < now)
        .values(status="expired", updated_at=now)
    )
    return int(result.rowcount or 0)
