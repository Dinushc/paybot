"""Internal API used by services: handoff, balance, consume, order status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, InitDataUse, Order, ServiceProduct
from app.security import SecurityError, init_data_replay_key, validate_init_data
from app.schemas import (
    ConsumeRequest,
    HandoffExchangeRequest,
    HandoffRequest,
    InitDataRequest,
)
from app.services.accounts import get_account, get_by_telegram_id
from app.services.catalog import (
    get_service_by_code,
    product_description,
    product_available,
    product_name,
    service_description,
    service_name,
)
from app.services.entitlements import consume, get_balance
from app.services.errors import DomainError, NotFoundError, ValidationError
from app.services.handoff import create_handoff, exchange_handoff
from app.api.deps import service_auth

router = APIRouter(prefix="/internal/v1", tags=["internal"])


def _require_account(db: Session, account_id: str) -> Account:
    account = get_account(db, account_id)
    if account is None or account.deleted_at is not None:
        raise NotFoundError("account not found")
    if account.is_blocked:
        raise DomainError("account_blocked", "account is blocked", status_code=403)
    return account


@router.post("/handoff")
def create_handoff_endpoint(
    body: HandoffRequest,
    auth=Depends(service_auth("handoff")),
    db: Session = Depends(get_db),
):
    _, service = auth
    if body.service_code != service.code:
        raise DomainError(
            "service_mismatch", "token belongs to another service", status_code=403
        )
    target = get_service_by_code(db, body.service_code)
    if target is None or target.id != service.id:
        raise DomainError("service_mismatch", "service not found", status_code=403)
    account = _require_account(db, body.account_id)
    return create_handoff(db, account=account, service=target)


@router.post("/handoff/exchange")
def exchange_handoff_endpoint(
    body: HandoffExchangeRequest,
    auth=Depends(service_auth("handoff")),
    db: Session = Depends(get_db),
):
    _, service = auth
    return exchange_handoff(db, token=body.token, service_id=service.id)


@router.post("/entitlements/consume")
def consume_endpoint(
    body: ConsumeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth=Depends(service_auth("entitlements:consume")),
    db: Session = Depends(get_db),
):
    _, service = auth
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header is required")
    _require_account(db, body.account_id)
    return consume(
        db,
        service_id=service.id,
        account_id=body.account_id,
        kind=body.kind,
        quantity=body.quantity,
        idempotency_key=idempotency_key,
        event_metadata=body.metadata,
    )


@router.get("/entitlements")
def balance_endpoint(
    account_id: str = Query(...),
    kind: str | None = Query(default=None),
    auth=Depends(service_auth("entitlements:read")),
    db: Session = Depends(get_db),
):
    _, service = auth
    _require_account(db, account_id)
    return get_balance(db, account_id=account_id, service_id=service.id, kind=kind)


@router.get("/orders/{order_id}")
def order_status_endpoint(
    order_id: str,
    auth=Depends(service_auth("orders:read")),
    db: Session = Depends(get_db),
):
    _, service = auth
    order = db.get(Order, order_id)
    if order is None or order.service_id != service.id:
        raise NotFoundError("order not found")
    return {
        "order_id": order.id,
        "status": order.status,
        "product_id": order.product_id,
        "price_stars": order.price_stars,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "refunded_at": order.refunded_at.isoformat() if order.refunded_at else None,
    }


@router.post("/orders/{order_id}/invoice-link")
def create_invoice_link_endpoint(
    order_id: str,
    auth=Depends(service_auth("orders:read")),
    db: Session = Depends(get_db),
):
    """Create a Stars invoice link for web/Mini App checkout of an existing order."""
    from app.telegram import TelegramError, get_bot

    _, service = auth
    order = db.get(Order, order_id)
    if order is None or order.service_id != service.id:
        raise NotFoundError("order not found")
    if order.status not in {"pending", "expired"}:
        raise DomainError("order_not_payable", "order is not payable", status_code=409)
    product = db.get(ServiceProduct, order.product_id)
    if not product_available(service, product):
        raise DomainError("product_unavailable", "product is not available", status_code=409)
    account = db.get(Account, order.account_id)
    lang = account.locale if account else "en"
    title = f"{service_name(service, lang)}: {product_name(product, lang)}"
    description = product_description(product, lang) or service_description(service, lang)
    try:
        url = get_bot().create_invoice_link(
            title=title,
            description=description or product_name(product, lang),
            payload=order.id,
            amount=order.price_stars,
            label=product_name(product, lang),
        )
    except TelegramError as exc:
        raise DomainError(
            "telegram_unavailable", f"could not create invoice link: {exc}", status_code=502
        ) from exc
    if not url:
        raise DomainError("telegram_unavailable", "Telegram returned no invoice link", status_code=502)
    order.invoice_url = url
    order.invoice_payload = order.id
    db.flush()
    return {"order_id": order.id, "invoice_url": url}


@router.post("/initdata/verify")
def verify_init_data_endpoint(
    body: InitDataRequest,
    auth=Depends(service_auth("handoff")),
    db: Session = Depends(get_db),
):
    try:
        data = validate_init_data(body.init_data)
    except SecurityError as exc:
        raise DomainError("invalid_init_data", str(exc), status_code=401) from exc
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    telegram_id = user.get("id")
    account = get_by_telegram_id(db, telegram_id) if telegram_id else None
    digest = init_data_replay_key(body.init_data)
    if db.get(InitDataUse, digest) is not None:
        raise DomainError("init_data_replayed", "initData was already used", status_code=401)
    try:
        with db.begin_nested():
            db.add(InitDataUse(digest=digest, account_id=account.id if account else None))
            db.flush()
    except IntegrityError as exc:
        raise DomainError(
            "init_data_replayed", "initData was already used", status_code=401
        ) from exc
    return {
        "valid": True,
        "auth_date": data.get("auth_date"),
        "user": user,
        "account_id": account.id if account else None,
        "locale": account.locale if account else None,
    }
