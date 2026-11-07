from __future__ import annotations

import hashlib
import hmac
import time
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import Entitlement, HandoffToken, Order, ServiceApiToken, utcnow
from app.services.admin_access import revoke_service_token
from tests.helpers import (
    USER_ID,
    buy_and_pay,
    grant_access,
    make_account,
    make_catalog,
    make_service_token,
)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_handoff_exchange_is_one_time(client):
    catalog = make_catalog()
    account_id = make_account()
    token = make_service_token(catalog["service_id"])

    resp = client.post(
        "/internal/v1/handoff",
        json={"account_id": account_id, "service_code": "photo"},
        headers=_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["expires_in"] == 60
    handoff = data["token"]

    resp = client.post(
        "/internal/v1/handoff/exchange", json={"token": handoff}, headers=_headers(token)
    )
    assert resp.status_code == 200
    assert resp.json()["account_id"] == account_id
    assert resp.json()["service_code"] == "photo"

    resp = client.post(
        "/internal/v1/handoff/exchange", json={"token": handoff}, headers=_headers(token)
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "handoff_replayed"


def test_handoff_expiry_and_service_scope(client):
    catalog = make_catalog()
    other = make_catalog("demo")
    account_id = make_account()
    token = make_service_token(catalog["service_id"])
    other_token = make_service_token(other["service_id"])

    resp = client.post(
        "/internal/v1/handoff",
        json={"account_id": account_id, "service_code": "photo"},
        headers=_headers(token),
    )
    handoff = resp.json()["token"]

    resp = client.post(
        "/internal/v1/handoff/exchange", json={"token": handoff}, headers=_headers(other_token)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "service_mismatch"

    with session_scope() as db:
        record = db.execute(select(HandoffToken)).scalar_one()
        record.expires_at = utcnow() - timedelta(seconds=1)
    resp = client.post(
        "/internal/v1/handoff/exchange", json={"token": handoff}, headers=_headers(token)
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "handoff_expired"

    # A token may only be created for its own service.
    resp = client.post(
        "/internal/v1/handoff",
        json={"account_id": account_id, "service_code": "demo"},
        headers=_headers(token),
    )
    assert resp.status_code == 403


def test_consume_is_idempotent_and_errors(client):
    catalog = make_catalog(quantity=2)
    account_id = make_account()
    grant_access(account_id, catalog)
    token = make_service_token(catalog["service_id"])
    body = {"account_id": account_id, "kind": "photo_check", "quantity": 1, "metadata": {"job": "1"}}

    resp = client.post(
        "/internal/v1/entitlements/consume",
        json=body,
        headers={**_headers(token), "Idempotency-Key": "key-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["remaining"] == 1
    assert resp.json()["balance"] == 1

    resp = client.post(
        "/internal/v1/entitlements/consume",
        json=body,
        headers={**_headers(token), "Idempotency-Key": "key-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["remaining"] == 1
    assert resp.json()["replayed"] is True

    with session_scope() as db:
        entitlement = db.execute(select(Entitlement)).scalar_one()
        assert entitlement.remaining == 1

    # Same key with a different body is a conflict.
    resp = client.post(
        "/internal/v1/entitlements/consume",
        json={**body, "quantity": 2},
        headers={**_headers(token), "Idempotency-Key": "key-1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "idempotency_conflict"

    # Missing idempotency key.
    resp = client.post(
        "/internal/v1/entitlements/consume", json=body, headers=_headers(token)
    )
    assert resp.status_code == 422

    # Not enough remaining.
    resp = client.post(
        "/internal/v1/entitlements/consume",
        json={**body, "quantity": 5},
        headers={**_headers(token), "Idempotency-Key": "key-2"},
    )
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "insufficient_entitlement"

    resp = client.get(
        "/internal/v1/entitlements",
        params={"account_id": account_id, "kind": "photo_check"},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["balance"] == 1


def test_consume_cannot_touch_another_service(client):
    catalog = make_catalog("photo")
    other = make_catalog("demo")
    account_id = make_account()
    grant_access(account_id, catalog)
    other_token = make_service_token(other["service_id"])
    resp = client.post(
        "/internal/v1/entitlements/consume",
        json={"account_id": account_id, "kind": "photo_check", "quantity": 1},
        headers={**_headers(other_token), "Idempotency-Key": "other-1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "service_mismatch"


def test_consume_expired_entitlement(client):
    catalog = make_catalog(quantity=1)
    account_id = make_account()
    grant_access(account_id, catalog, expires_at=utcnow() - timedelta(minutes=1))
    token = make_service_token(catalog["service_id"])
    resp = client.post(
        "/internal/v1/entitlements/consume",
        json={"account_id": account_id, "kind": "photo_check", "quantity": 1},
        headers={**_headers(token), "Idempotency-Key": "expired-1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "expired"


def test_revoked_and_foreign_tokens(client):
    catalog = make_catalog()
    account_id = make_account()
    token = make_service_token(catalog["service_id"])
    with session_scope() as db:
        record = db.execute(select(ServiceApiToken)).scalar_one()
        revoke_service_token(db, record.id)
    resp = client.get(
        "/internal/v1/entitlements",
        params={"account_id": account_id},
        headers=_headers(token),
    )
    assert resp.status_code == 401

    resp = client.get(
        "/internal/v1/entitlements",
        params={"account_id": account_id},
        headers=_headers("svc_invalid"),
    )
    assert resp.status_code == 401


def test_internal_invoice_link_for_pending_order(client, fake_bot):
    catalog = make_catalog()
    token = make_service_token(catalog["service_id"])
    from tests.helpers import callback_update, post_update

    post_update(client, callback_update(700, f"buy:{catalog['product_id']}"))
    with session_scope() as db:
        order_id = db.execute(select(Order)).scalar_one().id
    resp = client.post(
        f"/internal/v1/orders/{order_id}/invoice-link", headers=_headers(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invoice_url"] == "https://t.me/invoice/test"
    with session_scope() as db:
        assert db.get(Order, order_id).invoice_url == "https://t.me/invoice/test"


def test_internal_order_status_is_scoped(client, fake_bot):
    catalog = make_catalog(price=10)
    other = make_catalog("demo")
    token = make_service_token(catalog["service_id"])
    other_token = make_service_token(other["service_id"])
    paid = buy_and_pay(client, catalog)
    resp = client.get(
        f"/internal/v1/orders/{paid['order_id']}", headers=_headers(token)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"
    resp = client.get(
        f"/internal/v1/orders/{paid['order_id']}", headers=_headers(other_token)
    )
    assert resp.status_code == 404


def _init_data(user_id: int, auth_date: int, token: str = settings.telegram_bot_token) -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAExample",
        "user": f'{{"id":{user_id},"first_name":"Test"}}',
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    fields["hash"] = digest
    return urlencode(fields)


def test_init_data_verification(client):
    catalog = make_catalog()
    account_id = make_account()
    token = make_service_token(catalog["service_id"])
    init_data = _init_data(USER_ID, int(time.time()))
    resp = client.post(
        "/internal/v1/initdata/verify", json={"init_data": init_data}, headers=_headers(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_id"] == account_id
    assert resp.json()["valid"] is True

    replay = client.post(
        "/internal/v1/initdata/verify", json={"init_data": init_data}, headers=_headers(token)
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "init_data_replayed"

    reordered = "&".join(reversed(init_data.split("&")))
    replayed_reordered = client.post(
        "/internal/v1/initdata/verify",
        json={"init_data": reordered},
        headers=_headers(token),
    )
    assert replayed_reordered.status_code == 401
    assert replayed_reordered.json()["error"]["code"] == "init_data_replayed"

    expired = _init_data(USER_ID, int(time.time()) - 10000)
    resp = client.post(
        "/internal/v1/initdata/verify", json={"init_data": expired}, headers=_headers(token)
    )
    assert resp.status_code == 401

    tampered = init_data.replace("Test", "Hacked")
    resp = client.post(
        "/internal/v1/initdata/verify", json={"init_data": tampered}, headers=_headers(token)
    )
    assert resp.status_code == 401
