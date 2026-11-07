from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import (
    Account,
    Entitlement,
    EntitlementLedger,
    Order,
    OutboxEvent,
    Service,
    ServiceProduct,
    utcnow,
)
from app.services import outbox
from app.services.entitlements import expire_entitlements
from app.services.orders import expire_orders
from tests.helpers import (
    grant_access,
    make_account,
    make_catalog,
    message_update,
)


def test_webhook_secret_and_validation(client):
    update = message_update(1, "/start")
    assert client.post("/api/webhooks/telegram", json=update).status_code == 401
    assert (
        client.post(
            "/api/webhooks/telegram",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        ).status_code
        == 401
    )
    resp = client.post(
        "/api/webhooks/telegram",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret},
    )
    assert resp.status_code == 200

    resp = client.post(
        "/api/webhooks/telegram",
        content=b"x" * (settings.webhook_max_body_bytes + 1),
        headers={
            "X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 413


def test_production_config_guard():
    insecure = replace(
        settings,
        environment="production",
        public_base_url="http://example.com",
        secret_key="short",
        telegram_bot_token="123:abc",
        telegram_webhook_secret="",
    )
    problems = insecure.fatal_problems()
    assert any("https" in item for item in problems)
    assert any("SECRET_KEY" in item for item in problems)
    assert any("TELEGRAM_WEBHOOK_SECRET" in item for item in problems)

    secure = replace(
        settings,
        environment="production",
        public_base_url="https://example.com",
        secret_key="x" * 48,
        telegram_bot_token="123:abc",
        telegram_webhook_secret="secret",
    )
    assert secure.fatal_problems() == []


def test_outbox_notification_and_retry(client, fake_bot):
    catalog = make_catalog()
    account_id = make_account()
    with session_scope() as db:
        account = db.get(Account, account_id)
        outbox.enqueue_user_notification(
            db, account=account, key="balance_title", params={}
        )
    with session_scope() as db:
        stats = outbox.dispatch_once(db, fake_bot)
    assert stats["sent"] == 1
    assert fake_bot.calls_named("send_message")

    with session_scope() as db:
        service = db.get(Service, catalog["service_id"])
        service.events_url = "https://service.example/events"
        service.events_secret = "s3cret"
        event = outbox.add_event(
            db,
            event_type="order.paid",
            aggregate_id="agg",
            service_id=service.id,
            payload={"order_id": "agg"},
        )
        event_id = event.id

    class Response:
        status_code = 200

    sent = {}

    def fake_post(url, content=None, headers=None, timeout=None):
        sent["url"] = url
        sent["content"] = content
        sent["headers"] = headers
        return Response()

    import app.services.outbox as outbox_module

    original = outbox_module.httpx.post
    outbox_module.httpx.post = fake_post
    try:
        with session_scope() as db:
            stats = outbox.dispatch_once(db, fake_bot)
        assert stats["sent"] >= 1
    finally:
        outbox_module.httpx.post = original
    assert sent["url"] == "https://service.example/events"
    assert sent["headers"]["X-Platform-Signature"].startswith("sha256=")
    with session_scope() as db:
        assert db.get(OutboxEvent, event_id).status == "sent"


def test_outbox_goes_dead_and_alerts(client, fake_bot):
    catalog = make_catalog()
    with session_scope() as db:
        service = db.get(Service, catalog["service_id"])
        service.events_url = "https://service.example/events"
        event = outbox.add_event(
            db,
            event_type="order.paid",
            aggregate_id="agg",
            service_id=service.id,
            payload={},
        )
        event.attempts = settings.outbox_max_attempts - 1
        event_id = event.id

    class Response:
        status_code = 500

    import app.services.outbox as outbox_module

    original = outbox_module.httpx.post
    outbox_module.httpx.post = lambda *args, **kwargs: Response()
    try:
        with session_scope() as db:
            stats = outbox.dispatch_once(db, fake_bot)
    finally:
        outbox_module.httpx.post = original
    assert stats["dead"] == 1
    with session_scope() as db:
        assert db.get(OutboxEvent, event_id).status == "dead"
        alerts = [
            item
            for item in db.execute(select(OutboxEvent)).scalars()
            if item.payload.get("key") == "admin_outbox_dead"
        ]
        assert alerts


def test_expiry_jobs(client):
    catalog = make_catalog(quantity=2)
    account_id = make_account()
    with session_scope() as db:
        service = db.get(Service, catalog["service_id"])
        product = db.get(ServiceProduct, catalog["product_id"])
        account = db.get(Account, account_id)
        order = Order(
            account_id=account.id,
            service_id=service.id,
            product_id=product.id,
            status="pending",
            price_stars=product.price_stars,
            expires_at=utcnow() - timedelta(minutes=1),
        )
        db.add(order)
        db.flush()
        order_id = order.id
    with session_scope() as db:
        assert expire_orders(db) >= 1
    with session_scope() as db:
        assert db.get(Order, order_id).status == "expired"

    granted = grant_access(account_id, catalog, expires_at=utcnow() - timedelta(minutes=1))
    with session_scope() as db:
        expired = expire_entitlements(db)
    assert expired == 1
    with session_scope() as db:
        entitlement = db.get(Entitlement, granted["entitlement_id"])
        assert entitlement.status == "expired"
        assert entitlement.remaining == 0
        ledger = db.execute(
            select(EntitlementLedger).where(EntitlementLedger.reason == "expiry")
        ).scalar_one()
        assert ledger.delta == -2


def test_health_and_metrics(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").text == "ready"
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "updates_received" in resp.text or resp.text.strip() == ""


def test_admin_ui_login_and_csrf(client):
    resp = client.get("/admin/services", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"

    resp = client.post("/admin/login", data={"token": "bad"}, follow_redirects=False)
    assert resp.status_code == 401

    resp = client.post(
        "/admin/login",
        data={"token": settings.admin_api_token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    cookie = resp.cookies.get("paybot_admin")
    assert cookie

    page = client.get("/admin/services")
    assert page.status_code == 200
    assert "Services" in page.text
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match
    csrf = match.group(1)

    resp = client.post(
        "/admin/services",
        data={"code": "ui_svc", "name": "UI Service", "status": "draft"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    resp = client.post(
        "/admin/services",
        data={"code": "ui_svc", "name": "UI Service", "status": "draft", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with session_scope() as db:
        assert db.execute(select(Service).where(Service.code == "ui_svc")).scalar_one()

    for path in (
        "/admin/orders",
        "/admin/entitlements",
        "/admin/payments",
        "/admin/users?query=tester",
        "/admin/reports",
        "/admin/reports.csv",
        "/admin/audit",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.text[:200])
    resp = client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 303
