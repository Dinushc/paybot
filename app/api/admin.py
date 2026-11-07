"""Admin API: services, products, orders, refunds, entitlements, audit, reports."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Account,
    AdminApiKey,
    AuditLog,
    Entitlement,
    Order,
    PaymentEvent,
    Service,
    ServiceProduct,
    TelegramProfile,
)
from app.schemas import (
    AdjustRequest,
    AdminKeyCreate,
    ProductCreate,
    ProductPatch,
    RefundRequest,
    ServiceCreate,
    ServicePatch,
    TokenCreate,
)
from app.services import audit, payments, reports
from app.services.admin_access import (
    create_admin_key,
    create_service_token,
    revoke_service_token,
)
from app.services.entitlements import adjust
from app.services.errors import ConflictError, DomainError, NotFoundError, ValidationError
from app.api.deps import admin_auth

router = APIRouter(prefix="/api/admin", tags=["admin"])

SERVICE_STATUSES = {"draft", "active", "disabled"}
ORDER_STATUSES = {"pending", "paid", "review", "refunded", "expired", "failed"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _actor(key: AdminApiKey) -> str:
    return f"{key.role}:{key.name}:{key.id}"


def _check_https(value: str | None) -> None:
    if value and not value.startswith("https://"):
        raise ValidationError("URLs must use https://")


def _validate_service(service: Service) -> None:
    if service.status not in SERVICE_STATUSES:
        raise ValidationError("invalid service status")
    for field in (
        "webapp_url",
        "handoff_url",
        "events_url",
        "terms_url",
        "privacy_url",
        "support_url",
    ):
        _check_https(getattr(service, field))
    if service.platform_fee_bps < 0 or service.platform_fee_bps > 10000:
        raise ValidationError("platform_fee_bps must be between 0 and 10000")
    if service.status == "active":
        if not (service.terms_url and service.privacy_url):
            raise ValidationError("active services require terms_url and privacy_url")
        if not (service.support_email or service.support_url):
            raise ValidationError("active services require support_email or support_url")


def _validate_product(product: ServiceProduct) -> None:
    if product.price_stars < 1:
        raise ValidationError("price_stars must be at least 1")
    if product.entitlement_quantity < 1:
        raise ValidationError("entitlement_quantity must be positive")
    if not product.entitlement_kind.strip():
        raise ValidationError("entitlement_kind is required")


def _ensure_service_scope(key: AdminApiKey, service_id: str | None) -> None:
    if key.role == "service_admin" and key.service_id != service_id:
        raise DomainError("forbidden", "service scope mismatch", status_code=403)


def _service_dict(service: Service) -> dict:
    return {
        "id": service.id,
        "code": service.code,
        "name": service.name,
        "description": service.description,
        "emoji": service.emoji,
        "status": service.status,
        "webapp_url": service.webapp_url,
        "handoff_url": service.handoff_url,
        "events_url": service.events_url,
        "has_events_secret": bool(service.events_secret),
        "terms_url": service.terms_url,
        "privacy_url": service.privacy_url,
        "support_email": service.support_email,
        "support_url": service.support_url,
        "platform_fee_bps": service.platform_fee_bps,
        "sort_order": service.sort_order,
        "translations": service.translations or {},
        "created_at": _iso(service.created_at),
        "updated_at": _iso(service.updated_at),
    }


def _product_dict(product: ServiceProduct) -> dict:
    return {
        "id": product.id,
        "service_id": product.service_id,
        "code": product.code,
        "name": product.name,
        "description": product.description,
        "price_stars": product.price_stars,
        "entitlement_kind": product.entitlement_kind,
        "entitlement_quantity": product.entitlement_quantity,
        "entitlement_ttl_days": product.entitlement_ttl_days,
        "active": product.active,
        "sort_order": product.sort_order,
        "translations": product.translations or {},
    }


def _order_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "account_id": order.account_id,
        "service_id": order.service_id,
        "product_id": order.product_id,
        "provider": order.provider,
        "status": order.status,
        "price_stars": order.price_stars,
        "gross_stars": order.gross_stars,
        "platform_fee_stars": order.platform_fee_stars,
        "service_share_stars": order.service_share_stars,
        "telegram_user_id": order.telegram_user_id,
        "payment_id": order.payment_id,
        "review_reason": order.review_reason,
        "created_at": _iso(order.created_at),
        "expires_at": _iso(order.expires_at),
        "paid_at": _iso(order.paid_at),
        "refunded_at": _iso(order.refunded_at),
    }


def _audit_dict(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "actor": entry.actor,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "before": entry.before,
        "after": entry.after,
        "created_at": _iso(entry.created_at),
    }


def _entitlement_dict(entitlement: Entitlement) -> dict:
    return {
        "id": entitlement.id,
        "account_id": entitlement.account_id,
        "service_id": entitlement.service_id,
        "kind": entitlement.kind,
        "quantity": entitlement.quantity,
        "remaining": entitlement.remaining,
        "status": entitlement.status,
        "source_order_id": entitlement.source_order_id,
        "expires_at": _iso(entitlement.expires_at),
        "created_at": _iso(entitlement.created_at),
    }


# ---------------------------------------------------------------------------
# Services and products
# ---------------------------------------------------------------------------


@router.get("/services")
def list_services(
    key: AdminApiKey = Depends(admin_auth("services:read")),
    db: Session = Depends(get_db),
):
    query = select(Service).order_by(Service.sort_order, Service.name)
    if key.role == "service_admin":
        query = query.where(Service.id == key.service_id)
    return {"items": [_service_dict(item) for item in db.execute(query).scalars()]}


@router.post("/services", status_code=201)
def create_service(
    body: ServiceCreate,
    key: AdminApiKey = Depends(admin_auth("services:write")),
    db: Session = Depends(get_db),
):
    if key.role != "platform_admin":
        raise DomainError("forbidden", "only platform admins can create services", status_code=403)
    if db.execute(select(Service).where(Service.code == body.code)).scalar_one_or_none():
        raise ConflictError("service code is already in use", code="duplicate_code")
    service = Service(**body.model_dump())
    _validate_service(service)
    db.add(service)
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="service.create",
        entity_type="service",
        entity_id=service.id,
        after=_service_dict(service),
    )
    return _service_dict(service)


@router.patch("/services/{service_id}")
def update_service(
    service_id: str,
    body: ServicePatch,
    key: AdminApiKey = Depends(admin_auth("services:write")),
    db: Session = Depends(get_db),
):
    service = db.get(Service, service_id)
    if service is None:
        raise NotFoundError("service not found")
    _ensure_service_scope(key, service.id)
    before = _service_dict(service)
    changes = body.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"] != service.code:
        if db.execute(select(Service).where(Service.code == changes["code"])).scalar_one_or_none():
            raise ConflictError("service code is already in use", code="duplicate_code")
    for field, value in changes.items():
        setattr(service, field, value)
    _validate_service(service)
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="service.update",
        entity_type="service",
        entity_id=service.id,
        before=before,
        after=_service_dict(service),
    )
    return _service_dict(service)


@router.get("/services/{service_id}/products")
def list_products(
    service_id: str,
    key: AdminApiKey = Depends(admin_auth("products:read")),
    db: Session = Depends(get_db),
):
    _ensure_service_scope(key, service_id)
    products = db.execute(
        select(ServiceProduct)
        .where(ServiceProduct.service_id == service_id)
        .order_by(ServiceProduct.sort_order, ServiceProduct.price_stars)
    ).scalars()
    return {"items": [_product_dict(item) for item in products]}


@router.post("/services/{service_id}/products", status_code=201)
def create_product(
    service_id: str,
    body: ProductCreate,
    key: AdminApiKey = Depends(admin_auth("products:write")),
    db: Session = Depends(get_db),
):
    service = db.get(Service, service_id)
    if service is None:
        raise NotFoundError("service not found")
    _ensure_service_scope(key, service.id)
    exists = db.execute(
        select(ServiceProduct).where(
            ServiceProduct.service_id == service_id, ServiceProduct.code == body.code
        )
    ).scalar_one_or_none()
    if exists:
        raise ConflictError("product code is already in use", code="duplicate_code")
    product = ServiceProduct(service_id=service_id, **body.model_dump())
    _validate_product(product)
    db.add(product)
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="product.create",
        entity_type="product",
        entity_id=product.id,
        after=_product_dict(product),
    )
    return _product_dict(product)


@router.patch("/services/{service_id}/products/{product_id}")
def update_product(
    service_id: str,
    product_id: str,
    body: ProductPatch,
    key: AdminApiKey = Depends(admin_auth("products:write")),
    db: Session = Depends(get_db),
):
    _ensure_service_scope(key, service_id)
    product = db.get(ServiceProduct, product_id)
    if product is None or product.service_id != service_id:
        raise NotFoundError("product not found")
    before = _product_dict(product)
    changes = body.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"] != product.code:
        exists = db.execute(
            select(ServiceProduct).where(
                ServiceProduct.service_id == service_id,
                ServiceProduct.code == changes["code"],
            )
        ).scalar_one_or_none()
        if exists:
            raise ConflictError("product code is already in use", code="duplicate_code")
    for field, value in changes.items():
        setattr(product, field, value)
    _validate_product(product)
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="product.update",
        entity_type="product",
        entity_id=product.id,
        before=before,
        after=_product_dict(product),
    )
    return _product_dict(product)


# ---------------------------------------------------------------------------
# Orders, payments, refunds
# ---------------------------------------------------------------------------


@router.get("/orders")
def list_orders(
    status: str | None = Query(default=None),
    service_id: str | None = Query(default=None),
    telegram_user_id: int | None = Query(default=None),
    payment_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    key: AdminApiKey = Depends(admin_auth("orders:read")),
    db: Session = Depends(get_db),
):
    query = select(Order)
    if key.role == "service_admin":
        query = query.where(Order.service_id == key.service_id)
    if status:
        if status not in ORDER_STATUSES:
            raise ValidationError("invalid order status")
        query = query.where(Order.status == status)
    if service_id:
        _ensure_service_scope(key, service_id)
        query = query.where(Order.service_id == service_id)
    if telegram_user_id:
        query = query.where(Order.telegram_user_id == telegram_user_id)
    if payment_id:
        query = query.where(Order.payment_id == payment_id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    items = db.execute(
        query.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return {"total": total, "items": [_order_dict(item) for item in items]}


@router.get("/orders/{order_id}")
def get_order_api(
    order_id: str,
    key: AdminApiKey = Depends(admin_auth("orders:read")),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if order is None:
        raise NotFoundError("order not found")
    _ensure_service_scope(key, order.service_id)
    return _order_dict(order)


@router.post("/orders/{order_id}/refund")
def refund_order_api(
    order_id: str,
    body: RefundRequest | None = None,
    key: AdminApiKey = Depends(admin_auth("orders:refund")),
    db: Session = Depends(get_db),
):
    from app.telegram import get_bot

    order = db.get(Order, order_id)
    if order is None:
        raise NotFoundError("order not found")
    _ensure_service_scope(key, order.service_id)
    result = payments.refund_order(
        db, order_id=order_id, bot=get_bot(), actor=_actor(key)
    )
    return result


@router.post("/orders/{order_id}/resolve")
def resolve_order_api(
    order_id: str,
    action: str = Query(default="mark_paid"),
    key: AdminApiKey = Depends(admin_auth("orders:refund")),
    db: Session = Depends(get_db),
):
    if key.role != "platform_admin":
        raise DomainError("forbidden", "only platform admins can resolve review orders", status_code=403)
    order = db.get(Order, order_id)
    if order is None:
        raise NotFoundError("order not found")
    if action not in {"mark_paid", "refund"}:
        raise ValidationError("action must be mark_paid or refund")
    if action == "refund":
        from app.telegram import get_bot

        return payments.refund_order(db, order_id=order_id, bot=get_bot(), actor=_actor(key))
    if order.status != "review":
        raise DomainError("invalid_status", "only review orders can be resolved", status_code=409)
    has_access = db.execute(
        select(Entitlement).where(Entitlement.source_order_id == order.id)
    ).scalar_one_or_none()
    if has_access is None:
        raise DomainError(
            "no_entitlement",
            "the order has no granted access; use refund or grant access explicitly",
            status_code=409,
        )
    before = _order_dict(order)
    order.status = "paid" if order.payment_id else "failed"
    order.review_reason = None
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="order.resolve",
        entity_type="order",
        entity_id=order.id,
        before=before,
        after=_order_dict(order),
    )
    return _order_dict(order)


@router.get("/payment-events")
def list_payment_events(
    order_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    key: AdminApiKey = Depends(admin_auth("payments:read")),
    db: Session = Depends(get_db),
):
    query = select(PaymentEvent)
    if order_id:
        query = query.where(PaymentEvent.order_id == order_id)
    if status:
        query = query.where(PaymentEvent.status == status)
    items = db.execute(query.order_by(PaymentEvent.created_at.desc()).limit(limit)).scalars()
    return {
        "items": [
            {
                "id": item.id,
                "order_id": item.order_id,
                "service_id": item.service_id,
                "event_key": item.event_key.split(":", 1)[0] + ":***",
                "payment_id": (item.payment_id[:6] + "…") if item.payment_id else None,
                "status": item.status,
                "created_at": _iso(item.created_at),
            }
            for item in items
        ]
    }


# ---------------------------------------------------------------------------
# Entitlements, users, audit, reports
# ---------------------------------------------------------------------------


@router.get("/entitlements")
def list_entitlements(
    account_id: str | None = Query(default=None),
    service_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    key: AdminApiKey = Depends(admin_auth("entitlements:read")),
    db: Session = Depends(get_db),
):
    query = select(Entitlement)
    if key.role == "service_admin":
        query = query.where(Entitlement.service_id == key.service_id)
    if account_id:
        query = query.where(Entitlement.account_id == account_id)
    if service_id:
        _ensure_service_scope(key, service_id)
        query = query.where(Entitlement.service_id == service_id)
    if kind:
        query = query.where(Entitlement.kind == kind)
    if status:
        query = query.where(Entitlement.status == status)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    items = db.execute(
        query.order_by(Entitlement.created_at.desc()).limit(limit).offset(offset)
    ).scalars()
    return {"total": total, "items": [_entitlement_dict(item) for item in items]}


@router.post("/entitlements/{entitlement_id}/adjust")
def adjust_entitlement(
    entitlement_id: str,
    body: AdjustRequest,
    key: AdminApiKey = Depends(admin_auth("entitlements:adjust")),
    db: Session = Depends(get_db),
):
    entitlement = db.get(Entitlement, entitlement_id)
    if entitlement is None:
        raise NotFoundError("entitlement not found")
    _ensure_service_scope(key, entitlement.service_id)
    before = _entitlement_dict(entitlement)
    updated = adjust(
        db,
        entitlement_id=entitlement_id,
        delta=body.delta,
        actor=_actor(key),
        reason=body.reason,
    )
    audit.write_audit(
        db,
        actor=_actor(key),
        action="entitlement.adjust",
        entity_type="entitlement",
        entity_id=entitlement_id,
        before=before,
        after=_entitlement_dict(updated),
    )
    return _entitlement_dict(updated)


@router.get("/users")
def search_users(
    query: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(default=20, ge=1, le=100),
    key: AdminApiKey = Depends(admin_auth("users:read")),
    db: Session = Depends(get_db),
):
    like = f"%{query.strip()}%"
    statement = (
        select(Account, TelegramProfile)
        .outerjoin(TelegramProfile, TelegramProfile.account_id == Account.id)
        .where(
            or_(
                Account.email.ilike(like),
                Account.name.ilike(like),
                TelegramProfile.username.ilike(like),
                TelegramProfile.telegram_user_id == (
                    int(query) if query.strip().isdigit() else -1
                ),
            )
        )
        .limit(limit)
    )
    items = []
    for account, profile in db.execute(statement):
        items.append(
            {
                "account_id": account.id,
                "email": account.email,
                "name": account.name,
                "locale": account.locale,
                "is_blocked": account.is_blocked,
                "telegram_user_id": profile.telegram_user_id if profile else None,
                "username": profile.username if profile else None,
                "created_at": _iso(account.created_at),
            }
        )
    return {"items": items}


@router.post("/users/{account_id}/anonymize")
def anonymize_user(
    account_id: str,
    key: AdminApiKey = Depends(admin_auth("users:anonymize")),
    db: Session = Depends(get_db),
):
    from app.models import utcnow

    account = db.get(Account, account_id)
    if account is None:
        raise NotFoundError("account not found")
    before = {
        "email": account.email,
        "name": account.name,
        "is_blocked": account.is_blocked,
        "deleted_at": _iso(account.deleted_at),
    }
    account.email = None
    account.name = None
    account.is_blocked = True
    account.deleted_at = utcnow()
    profile = db.get(TelegramProfile, account.id)
    if profile is not None:
        profile.username = None
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="account.anonymize",
        entity_type="account",
        entity_id=account.id,
        before=before,
        after={
            "email": None,
            "name": None,
            "is_blocked": True,
            "deleted_at": _iso(account.deleted_at),
        },
    )
    return {"account_id": account.id, "anonymized": True, "deleted_at": _iso(account.deleted_at)}


@router.get("/audit")
def list_audit(
    entity_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    key: AdminApiKey = Depends(admin_auth("audit:read")),
    db: Session = Depends(get_db),
):
    query = select(AuditLog)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if action:
        query = query.where(AuditLog.action == action)
    items = db.execute(query.order_by(AuditLog.created_at.desc()).limit(limit)).scalars()
    return {"items": [_audit_dict(item) for item in items]}


@router.get("/reports/revenue")
def revenue_report(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    service_id: str | None = Query(default=None),
    fmt: str | None = Query(default=None, alias="format"),
    key: AdminApiKey = Depends(admin_auth("reports:read")),
    db: Session = Depends(get_db),
):
    if key.role == "service_admin":
        service_id = key.service_id
    elif service_id:
        _ensure_service_scope(key, service_id)
    report = reports.revenue(
        db, date_from=date_from, date_to=date_to, service_id=service_id
    )
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "service_code",
                "service_name",
                "orders",
                "gross_stars",
                "platform_fee_stars",
                "service_share_stars",
                "refund_orders",
                "refunded_stars",
            ]
        )
        for row in report["rows"]:
            writer.writerow(
                [
                    row["service_code"],
                    row["service_name"],
                    row["orders"],
                    row["gross_stars"],
                    row["platform_fee_stars"],
                    row["service_share_stars"],
                    row["refund_orders"],
                    row["refunded_stars"],
                ]
            )
        return Response(buffer.getvalue(), media_type="text/csv")
    return report


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@router.post("/services/{service_id}/tokens", status_code=201)
def issue_service_token(
    service_id: str,
    body: TokenCreate,
    key: AdminApiKey = Depends(admin_auth("tokens:write")),
    db: Session = Depends(get_db),
):
    if key.role != "platform_admin":
        raise DomainError("forbidden", "only platform admins can issue service tokens", status_code=403)
    service = db.get(Service, service_id)
    if service is None:
        raise NotFoundError("service not found")
    record, token = create_service_token(db, service_id=service_id, scopes=body.scopes)
    audit.write_audit(
        db,
        actor=_actor(key),
        action="service_token.create",
        entity_type="service_token",
        entity_id=record.id,
        after={"service_id": service_id, "scopes": record.scopes},
    )
    return {
        "id": record.id,
        "service_id": service_id,
        "scopes": record.scopes,
        "token": token,
        "warning": "the token is shown only once",
    }


@router.delete("/tokens/{token_id}")
def revoke_token(
    token_id: str,
    key: AdminApiKey = Depends(admin_auth("tokens:write")),
    db: Session = Depends(get_db),
):
    if key.role != "platform_admin":
        raise DomainError("forbidden", "only platform admins can revoke service tokens", status_code=403)
    record = revoke_service_token(db, token_id)
    audit.write_audit(
        db,
        actor=_actor(key),
        action="service_token.revoke",
        entity_type="service_token",
        entity_id=record.id,
        after={"service_id": record.service_id},
    )
    return {"id": record.id, "revoked_at": _iso(record.revoked_at)}


@router.post("/keys", status_code=201)
def create_key(
    body: AdminKeyCreate,
    key: AdminApiKey = Depends(admin_auth("keys:write")),
    db: Session = Depends(get_db),
):
    if key.role != "platform_admin":
        raise DomainError("forbidden", "only platform admins can create keys", status_code=403)
    if body.service_id:
        service = db.get(Service, body.service_id)
        if service is None:
            raise NotFoundError("service not found")
    record, plain = create_admin_key(
        db,
        name=body.name,
        role=body.role,
        service_id=body.service_id,
        token=body.token,
    )
    audit.write_audit(
        db,
        actor=_actor(key),
        action="admin_key.create",
        entity_type="admin_key",
        entity_id=record.id,
        after={"role": record.role, "service_id": record.service_id},
    )
    return {
        "id": record.id,
        "role": record.role,
        "token": plain,
        "warning": "the token is shown only once",
    }


@router.delete("/keys/{key_id}")
def revoke_key(
    key_id: str,
    key: AdminApiKey = Depends(admin_auth("keys:write")),
    db: Session = Depends(get_db),
):
    if key.role != "platform_admin":
        raise DomainError("forbidden", "only platform admins can revoke keys", status_code=403)
    record = db.get(AdminApiKey, key_id)
    if record is None:
        raise NotFoundError("admin key not found")
    from app.models import utcnow

    record.revoked_at = utcnow()
    db.flush()
    audit.write_audit(
        db,
        actor=_actor(key),
        action="admin_key.revoke",
        entity_type="admin_key",
        entity_id=record.id,
        after={"revoked_at": _iso(record.revoked_at)},
    )
    return {"id": record.id, "revoked_at": _iso(record.revoked_at)}
