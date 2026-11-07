from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import session_scope
from app.models import Account, Entitlement, Order, OutboxEvent, PaymentEvent, ServiceProduct, utcnow
from tests.conftest import ADMIN_HEADERS
from tests.helpers import (
    USER_ID,
    buy_and_pay,
    callback_update,
    make_catalog,
    message_update,
    payment_update,
    post_update,
    pre_checkout_update,
    tg_user,
)


def test_catalog_buy_and_pay_once(client, fake_bot):
    catalog = make_catalog(price=10, quantity=2)

    resp = post_update(client, message_update(1, "/start"))
    assert resp.status_code == 200
    texts = [call[1][1] for call in fake_bot.calls_named("send_message")]
    assert any("Service photo" in text for text in texts)

    resp = post_update(client, callback_update(2, f"svc:{catalog['service_id']}"))
    assert resp.status_code == 200
    last = fake_bot.calls_named("send_message")[-1]
    assert "Pack" in last[1][1]
    buy_buttons = [
        button
        for row in last[2]["reply_markup"]["inline_keyboard"]
        for button in row
        if button.get("callback_data", "").startswith("buy:")
    ]
    assert buy_buttons

    resp = post_update(client, callback_update(3, f"buy:{catalog['product_id']}"))
    assert resp.status_code == 200
    invoices = fake_bot.calls_named("send_invoice")
    assert len(invoices) == 1
    invoice = invoices[0][2]
    assert invoice["amount"] == 10
    assert invoice["payload"]

    with session_scope() as db:
        order = db.execute(select(Order)).scalar_one()
        assert order.status == "pending"
        assert order.price_stars == 10
        assert order.telegram_user_id == USER_ID
        assert order.invoice_payload == order.id
        order_id = order.id

    result = post_update(client, pre_checkout_update(4, order_id, 10))
    assert result.status_code == 200
    pre = fake_bot.calls_named("answer_pre_checkout")[-1]
    assert pre[1][1] is True

    result = post_update(client, payment_update(5, order_id, 10, "charge-abc"))
    assert result.status_code == 200
    with session_scope() as db:
        order = db.get(Order, order_id)
        assert order.status == "paid"
        assert order.payment_id == "charge-abc"
        assert order.gross_stars == 10
        assert order.platform_fee_stars == 1
        assert order.service_share_stars == 9
        entitlement = db.execute(select(Entitlement)).scalar_one()
        assert entitlement.remaining == 2
        assert entitlement.quantity == 2
        event = db.execute(select(PaymentEvent)).scalar_one()
        assert event.status == "paid"
        outbox_types = {item.event_type for item in db.execute(select(OutboxEvent)).scalars()}
        assert "order.paid" in outbox_types

    # Same update again: update_id idempotency.
    result = post_update(client, payment_update(5, order_id, 10, "charge-abc"))
    assert result.status_code == 200
    # Same charge with a new update id: payment_events idempotency.
    result = post_update(client, payment_update(6, order_id, 10, "charge-abc"))
    assert result.status_code == 200
    with session_scope() as db:
        entitlement = db.execute(select(Entitlement)).scalar_one()
        assert entitlement.remaining == 2
        assert len(list(db.execute(select(PaymentEvent)).scalars())) == 1


def test_double_click_reuses_recent_order(client, fake_bot):
    catalog = make_catalog()
    post_update(client, callback_update(10, f"buy:{catalog['product_id']}"))
    post_update(client, callback_update(11, f"buy:{catalog['product_id']}"))
    with session_scope() as db:
        orders = list(db.execute(select(Order)).scalars())
        assert len(orders) == 1
    assert len(fake_bot.calls_named("send_invoice")) == 2


def test_amount_mismatch_goes_to_review_without_grant(client, fake_bot):
    catalog = make_catalog(price=10)
    post_update(client, callback_update(20, f"buy:{catalog['product_id']}"))
    with session_scope() as db:
        order_id = db.execute(select(Order)).scalar_one().id

    resp = post_update(client, pre_checkout_update(21, order_id, 5))
    assert resp.status_code == 200
    assert fake_bot.calls_named("answer_pre_checkout")[-1][1][1] is False

    resp = post_update(client, payment_update(22, order_id, 5, "charge-wrong"))
    assert resp.status_code == 200
    with session_scope() as db:
        order = db.get(Order, order_id)
        assert order.status == "review"
        assert order.review_reason == "amount_mismatch"
        assert db.execute(select(Entitlement)).scalar_one_or_none() is None
        event = db.execute(select(PaymentEvent)).scalar_one()
        assert event.status == "amount_mismatch"
        alerts = [
            item
            for item in db.execute(select(OutboxEvent)).scalars()
            if item.payload.get("key") == "admin_amount_mismatch"
        ]
        assert alerts


def test_unknown_order_payment_alerts_admin(client, fake_bot):
    resp = post_update(client, payment_update(30, "does-not-exist", 10, "charge-unknown"))
    assert resp.status_code == 200
    with session_scope() as db:
        event = db.execute(select(PaymentEvent)).scalar_one()
        assert event.status == "unknown_order"
        alerts = [
            item
            for item in db.execute(select(OutboxEvent)).scalars()
            if item.payload.get("key") == "admin_unknown_order"
        ]
        assert alerts


def test_pre_checkout_rejects_inactive_product_and_blocked_user(client, fake_bot):
    catalog = make_catalog()
    post_update(client, callback_update(40, f"buy:{catalog['product_id']}"))
    with session_scope() as db:
        order_id = db.execute(select(Order)).scalar_one().id
        product = db.get(ServiceProduct, catalog["product_id"])
        product.active = False

    resp = post_update(client, pre_checkout_update(41, order_id, 10))
    assert resp.status_code == 200
    assert fake_bot.calls_named("answer_pre_checkout")[-1][1][1] is False
    assert "unavailable" in (fake_bot.calls_named("answer_pre_checkout")[-1][1][2] or "").lower()

    with session_scope() as db:
        product = db.get(ServiceProduct, catalog["product_id"])
        product.active = True
        account = db.execute(select(Account)).scalar_one()
        account.is_blocked = True
    resp = post_update(client, pre_checkout_update(42, order_id, 10))
    assert fake_bot.calls_named("answer_pre_checkout")[-1][1][1] is False


def test_late_payment_policy(client, fake_bot):
    catalog = make_catalog()
    post_update(client, callback_update(50, f"buy:{catalog['product_id']}"))
    with session_scope() as db:
        order = db.execute(select(Order)).scalar_one()
        order.status = "expired"
        order.expires_at = utcnow() - timedelta(minutes=5)
        order_id = order.id
    post_update(client, pre_checkout_update(51, order_id, 10))
    assert fake_bot.calls_named("answer_pre_checkout")[-1][1][1] is True

    with session_scope() as db:
        order = db.get(Order, order_id)
        order.status = "expired"
        order.expires_at = utcnow() - timedelta(days=7)
    post_update(client, pre_checkout_update(52, order_id, 10))
    assert fake_bot.calls_named("answer_pre_checkout")[-1][1][1] is False


def test_bot_language_balance_orders_and_paysupport(client, fake_bot):
    catalog = make_catalog(price=10, quantity=3)
    post_update(client, message_update(60, "/start"))
    resp = post_update(client, callback_update(61, "lang:ru"))
    assert resp.status_code == 200
    with session_scope() as db:
        account = db.execute(select(Account)).scalar_one()
        assert account.locale == "ru"

    post_update(client, message_update(62, "/balance"))
    assert "нет активного доступа" in fake_bot.calls_named("send_message")[-1][1][1]

    buy_and_pay(client, catalog)
    post_update(client, message_update(70, "/balance"))
    text = fake_bot.calls_named("send_message")[-1][1][1]
    assert "Service photo: 3" in text

    post_update(client, message_update(71, "/orders"))
    text = fake_bot.calls_named("send_message")[-1][1][1]
    assert "оплачен" in text

    with session_scope() as db:
        order_id = db.execute(select(Order)).scalar_one().id
    post_update(client, message_update(72, f"/paysupport {order_id}"))
    assert order_id in fake_bot.calls_named("send_message")[-1][1][1]

    post_update(client, message_update(73, f"/paysupport {order_id}", tg_user(OTHER_ID, lang="ru")))
    assert "не найден" in fake_bot.calls_named("send_message")[-1][1][1]


def test_start_deep_links(client, fake_bot):
    catalog = make_catalog()
    post_update(client, message_update(80, "/start svc_photo"))
    assert "Service photo" in fake_bot.calls_named("send_message")[-1][1][1]
    post_update(client, message_update(81, f"/start buy_{catalog['product_id']}"))
    assert fake_bot.calls_named("send_invoice")
    post_update(client, message_update(82, "/start svc_missing"))
    assert "not available" in fake_bot.calls_named("send_message")[-1][1][1].lower()


OTHER_ID = 2002


def test_stale_pending_order_bypasses_ttl_after_fix(client, fake_bot):
    catalog = make_catalog(price=10)
    post_update(client, callback_update(300, f"buy:{catalog['product_id']}"))
    with session_scope() as db:
        order = db.execute(select(Order)).scalar_one()
        order.status = "pending"
        order.expires_at = utcnow() - timedelta(days=2)
        order_id = order.id

    post_update(client, pre_checkout_update(301, order_id, 10))
    assert fake_bot.calls_named("answer_pre_checkout")[-1][1][1] is False
    assert "expired" in (fake_bot.calls_named("answer_pre_checkout")[-1][1][2] or "").lower()

    post_update(client, payment_update(302, order_id, 10, "charge-stale"))
    with session_scope() as db:
        order = db.get(Order, order_id)
        assert order.status == "review"
        assert order.review_reason == "late_payment_expired"
        assert db.execute(select(Entitlement)).scalar_one_or_none() is None


def test_payer_mismatch_refund_uses_actual_payer(client, fake_bot):
    catalog = make_catalog(price=10)
    buyer = tg_user(USER_ID)
    other = tg_user(2002, username="other")
    post_update(client, callback_update(310, f"buy:{catalog['product_id']}", buyer))
    with session_scope() as db:
        order = db.execute(select(Order)).scalar_one()
        order_id = order.id

    post_update(client, payment_update(311, order_id, 10, "charge-wrong-payer", other))
    with session_scope() as db:
        order = db.get(Order, order_id)
        assert order.status == "review"
        assert order.review_reason == "payer_mismatch"
        assert order.payment_id is None
        event = db.execute(
            select(PaymentEvent).where(PaymentEvent.payment_id == "charge-wrong-payer")
        ).scalar_one()
        assert event.payer_telegram_id == 2002
        event_id = event.id

    resp = client.post(
        f"/api/admin/payment-events/{event_id}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert fake_bot.calls_named("refund_star_payment")[-1][1] == (2002, "charge-wrong-payer")


def test_duplicate_charge_refundable_by_event(client, fake_bot):
    catalog = make_catalog(price=10)
    paid = buy_and_pay(client, catalog, charge="charge-first")
    post_update(
        client,
        payment_update(320, paid["order_id"], 10, "charge-second"),
    )
    with session_scope() as db:
        order = db.get(Order, paid["order_id"])
        assert order.status == "paid"
        assert order.payment_id == "charge-first"
        event = db.execute(
            select(PaymentEvent).where(PaymentEvent.payment_id == "charge-second")
        ).scalar_one()
        assert event.status == "duplicate_payment"
        event_id = event.id

    resp = client.post(
        f"/api/admin/payment-events/{event_id}/refund", json={}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    with session_scope() as db:
        event = db.get(PaymentEvent, event_id)
        assert event.refunded_at is not None
    assert fake_bot.calls_named("refund_star_payment")[-1][1] == (USER_ID, "charge-second")
