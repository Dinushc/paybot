from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.db import session_scope
from app.models import Account, Order, Service, ServiceProduct, utcnow
from app.services.accounts import get_or_create_telegram_account
from app.services.admin_access import create_service_token
from app.services.entitlements import grant_for_order

USER_ID = 1001


def tg_user(user_id: int = USER_ID, lang: str = "en", username: str = "tester") -> dict:
    return {
        "id": user_id,
        "is_bot": False,
        "first_name": "Test",
        "username": username,
        "language_code": lang,
    }


def make_catalog(
    code: str = "photo",
    *,
    service_status: str = "active",
    product_active: bool = True,
    price: int = 10,
    quantity: int = 2,
    kind: str = "photo_check",
    ttl_days: int | None = None,
    fee_bps: int = 1000,
) -> dict:
    with session_scope() as db:
        service = Service(
            code=code,
            name=f"Service {code}",
            description=f"Description for {code}",
            status=service_status,
            webapp_url="https://example.com/app",
            terms_url="https://example.com/terms",
            privacy_url="https://example.com/privacy",
            support_email="support@example.com",
            platform_fee_bps=fee_bps,
        )
        db.add(service)
        db.flush()
        product = ServiceProduct(
            service_id=service.id,
            code="solo",
            name="Pack",
            description="Test pack",
            price_stars=price,
            entitlement_kind=kind,
            entitlement_quantity=quantity,
            entitlement_ttl_days=ttl_days,
            active=product_active,
        )
        db.add(product)
        db.flush()
        return {
            "service_id": service.id,
            "product_id": product.id,
            "code": code,
            "price": price,
            "quantity": quantity,
            "kind": kind,
        }


def make_account(user_id: int = USER_ID, lang: str = "en") -> str:
    with session_scope() as db:
        account = get_or_create_telegram_account(db, tg_user(user_id, lang))
        return account.id


def make_service_token(service_id: str, scopes: list[str] | None = None) -> str:
    with session_scope() as db:
        _, token = create_service_token(db, service_id=service_id, scopes=scopes)
        return token


def grant_access(
    account_id: str,
    catalog: dict,
    *,
    quantity: int | None = None,
    expires_at: datetime | None = None,
    user_id: int = USER_ID,
) -> dict:
    with session_scope() as db:
        service = db.get(Service, catalog["service_id"])
        product = db.get(ServiceProduct, catalog["product_id"])
        account = db.get(Account, account_id)
        order = Order(
            account_id=account.id,
            service_id=service.id,
            product_id=product.id,
            status="paid",
            price_stars=product.price_stars,
            telegram_user_id=user_id,
            paid_at=utcnow(),
        )
        db.add(order)
        db.flush()
        order.payment_id = f"test-charge-{order.id}"
        entitlement = grant_for_order(db, order=order, service=service, product=product)
        if quantity is not None:
            entitlement.quantity = quantity
            entitlement.remaining = quantity
        if expires_at is not None:
            entitlement.expires_at = expires_at
        db.flush()
        return {"order_id": order.id, "entitlement_id": entitlement.id}


def message_update(update_id: int, text: str, user: dict | None = None) -> dict:
    user = user or tg_user()
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": user,
            "chat": {"id": user["id"], "type": "private"},
            "date": int(time.time()),
            "text": text,
        },
    }


def callback_update(update_id: int, data: str, user: dict | None = None) -> dict:
    user = user or tg_user()
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": user,
            "message": {
                "message_id": 1,
                "chat": {"id": user["id"], "type": "private"},
                "date": int(time.time()),
            },
            "data": data,
        },
    }


def pre_checkout_update(
    update_id: int,
    payload: str,
    amount: int,
    user: dict | None = None,
    *,
    currency: str = "XTR",
    provider: str = "telegram_stars",
) -> dict:
    user = user or tg_user()
    return {
        "update_id": update_id,
        "pre_checkout_query": {
            "id": f"pc-{update_id}",
            "from": user,
            "currency": currency,
            "total_amount": amount,
            "invoice_payload": payload,
            "shipping_option_id": None,
            "order_info": None,
        },
    }


def payment_update(
    update_id: int,
    payload: str,
    amount: int,
    charge: str,
    user: dict | None = None,
    *,
    currency: str = "XTR",
) -> dict:
    user = user or tg_user()
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": user,
            "chat": {"id": user["id"], "type": "private"},
            "date": int(time.time()),
            "successful_payment": {
                "currency": currency,
                "total_amount": amount,
                "invoice_payload": payload,
                "telegram_payment_charge_id": charge,
                "provider_payment_charge_id": "provider-charge",
            },
        },
    }


def post_update(client, update: dict, headers: dict | None = None):
    return client.post(
        "/api/webhooks/telegram",
        json=update,
        headers=headers or {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"},
    )


def buy_and_pay(client, catalog: dict, *, charge: str = "charge-1", user_id: int = USER_ID) -> dict:
    """Drive the bot through buy -> pre-checkout -> successful_payment."""
    user = tg_user(user_id)
    resp = post_update(client, callback_update(900, f"buy:{catalog['product_id']}", user))
    assert resp.status_code == 200
    with session_scope() as db:
        order = db.query(Order).order_by(Order.created_at.desc()).first()
        assert order is not None
        order_id = order.id
        amount = order.price_stars
    resp = post_update(client, pre_checkout_update(901, order_id, amount, user))
    assert resp.status_code == 200
    resp = post_update(client, payment_update(902, order_id, amount, charge, user))
    assert resp.status_code == 200
    return {"order_id": order_id, "amount": amount}


def future(days: int = 1) -> datetime:
    return utcnow() + timedelta(days=days)
