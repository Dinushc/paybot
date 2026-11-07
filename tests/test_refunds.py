from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import session_scope
from app.models import Entitlement, Order, OutboxEvent, utcnow
from app.telegram import TelegramError
from tests.conftest import ADMIN_HEADERS
from tests.helpers import buy_and_pay, grant_access, make_account, make_catalog, make_service_token


def test_refund_revokes_unused_entitlement(client, fake_bot):
    catalog = make_catalog(price=10, quantity=2)
    paid = buy_and_pay(client, catalog)
    resp = client.post(
        f"/api/admin/orders/{paid['order_id']}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "refunded"
    assert len(resp.json()["revoked_entitlements"]) == 1
    assert fake_bot.calls_named("refund_star_payment")
    with session_scope() as db:
        order = db.get(Order, paid["order_id"])
        assert order.status == "refunded"
        entitlement = db.execute(select(Entitlement)).scalar_one()
        assert entitlement.status == "revoked"
        assert entitlement.remaining == 0
        events = [item.event_type for item in db.execute(select(OutboxEvent)).scalars()]
        assert "order.refunded" in events


def test_refund_does_not_claw_back_used_access(client, fake_bot):
    catalog = make_catalog(price=10, quantity=1)
    account_id = make_account()
    granted = grant_access(account_id, catalog, quantity=1)
    token = make_service_token(catalog["service_id"])
    resp = client.post(
        "/internal/v1/entitlements/consume",
        json={"account_id": account_id, "kind": "photo_check", "quantity": 1},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "used-1"},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/admin/orders/{granted['order_id']}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_entitlements"] == []
    with session_scope() as db:
        entitlement = db.execute(select(Entitlement)).scalar_one()
        assert entitlement.status == "exhausted"
        assert entitlement.remaining == 0


def test_refund_failure_keeps_order_paid(client, fake_bot):
    catalog = make_catalog(price=10)
    paid = buy_and_pay(client, catalog)
    fake_bot.refund_error = TelegramError("telegram down", status_code=500)
    resp = client.post(
        f"/api/admin/orders/{paid['order_id']}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "telegram_refund_failed"
    with session_scope() as db:
        order = db.get(Order, paid["order_id"])
        assert order.status == "paid"
        entitlement = db.execute(select(Entitlement)).scalar_one()
        assert entitlement.status == "active"
        assert entitlement.remaining == 2


def test_second_refund_conflicts(client, fake_bot):
    catalog = make_catalog(price=10)
    paid = buy_and_pay(client, catalog)
    resp = client.post(
        f"/api/admin/orders/{paid['order_id']}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/admin/orders/{paid['order_id']}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "already_refunded"


def test_refund_report_uses_refund_date(client, fake_bot):
    catalog = make_catalog(price=10)
    paid = buy_and_pay(client, catalog)
    with session_scope() as db:
        order = db.get(Order, paid["order_id"])
        order.paid_at = utcnow() - timedelta(days=10)
    resp = client.post(
        f"/api/admin/orders/{paid['order_id']}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    resp = client.get(
        "/api/admin/reports/revenue",
        params={"date_from": (utcnow() - timedelta(hours=1)).isoformat()},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    row = next(item for item in resp.json()["rows"] if item["service_code"] == "photo")
    assert row["orders"] == 0
    assert row["refund_orders"] == 1
    assert row["refunded_stars"] == 10
