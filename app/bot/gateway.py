"""Bot update routing: commands, catalog, invoices, callbacks."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import metrics
from app.config import settings
from app.db import session_scope
from app.i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES, status_label, t
from app.models import Account, BotUpdate, Order, Service, ServiceProduct, TelegramProfile
from app.services import catalog, orders as orders_service, outbox, payments
from app.services.accounts import get_or_create_telegram_account, set_locale
from app.services.entitlements import balance_by_service
from app.services.errors import DomainError
from app.services.handoff import create_handoff
from app.telegram import TelegramError, get_bot

log = logging.getLogger("app.bot")


# ---------------------------------------------------------------------------
# Keyboards and text builders
# ---------------------------------------------------------------------------


def _support_contact(service: Service | None = None) -> str:
    if service is not None:
        if service.support_url:
            return service.support_url
        if service.support_email:
            return service.support_email
    return settings.support_url or settings.support_email


def _nav_keyboard(lang: str) -> list[list[dict]]:
    return [
        [
            {"text": t(lang, "btn_services"), "callback_data": "services"},
            {"text": t(lang, "btn_orders"), "callback_data": "orders"},
        ],
        [
            {"text": t(lang, "btn_support"), "callback_data": "support"},
            {"text": t(lang, "btn_language"), "callback_data": "language"},
        ],
    ]


def _catalog_text(db: Session, account: Account, lang: str, *, greeting: bool) -> str:
    services = catalog.active_services(db)
    lines = [t(lang, "welcome" if greeting else "services_title")]
    if not services:
        lines.append(t(lang, "services_empty"))
        return "\n".join(lines)
    lines.append("")
    for service in services:
        label = f"{service.emoji} {catalog.service_name(service, lang)}".strip()
        lines.append(f"• {label}")
    balances = []
    for service in services:
        balance = catalog.service_balance(db, account.id, service.id)
        if balance > 0:
            balances.append(
                (t(lang, "balance_line", service=catalog.service_name(service, lang), balance=balance))
            )
    if balances:
        lines.extend(["", *balances])
    return "\n".join(lines)


def _catalog_keyboard(db: Session, lang: str) -> dict:
    rows = []
    for service in catalog.active_services(db):
        name = catalog.service_name(service, lang)
        label = f"{service.emoji} {name}".strip() if service.emoji else name
        rows.append([{"text": label[:60], "callback_data": f"svc:{service.id}"}])
    rows.extend(_nav_keyboard(lang))
    return {"inline_keyboard": rows}


def _service_text(db: Session, service: Service, lang: str) -> str:
    name = catalog.service_name(service, lang)
    description = catalog.service_description(service, lang)
    lines = [t(lang, "service_card", name=name, description=description)]
    products = catalog.active_products(db, service.id)
    if products:
        lines.extend(["", t(lang, "service_products")])
        for product in products:
            lines.append(
                t(
                    lang,
                    "product_line",
                    name=catalog.product_name(product, lang),
                    price=product.price_stars,
                )
            )
    else:
        lines.append(t(lang, "service_no_products"))
    links = []
    if service.terms_url:
        links.append(t(lang, "service_terms", url=service.terms_url))
    contact = _support_contact(service)
    if contact:
        links.append(t(lang, "service_support", contact=contact))
    if links:
        lines.extend(["", *links])
    return "\n".join(lines)


def _service_keyboard(
    db: Session, account: Account, service: Service, lang: str
) -> dict:
    rows: list[list[dict]] = []
    if catalog.has_access(db, account.id, service.id):
        rows.append([{"text": t(lang, "btn_open"), "callback_data": f"open:{service.id}"}])
    for product in catalog.active_products(db, service.id):
        label = f"{t(lang, 'btn_buy')}: {catalog.product_name(product, lang)}"
        rows.append(
            [
                {
                    "text": label[:60],
                    "callback_data": f"buy:{product.id}",
                }
            ]
        )
    rows.append([{"text": t(lang, "btn_back"), "callback_data": "services"}])
    rows.extend(_nav_keyboard(lang)[:1])
    return {"inline_keyboard": rows}


def _language_keyboard() -> dict:
    codes = list(SUPPORTED_LANGUAGES)
    rows = []
    for index in range(0, len(codes), 2):
        rows.append(
            [
                {"text": LANGUAGE_NAMES[code], "callback_data": f"lang:{code}"}
                for code in codes[index : index + 2]
            ]
        )
    return {"inline_keyboard": rows}


def _message(chat_id: int | str, text: str, markup: dict | None = None) -> dict:
    action: dict[str, Any] = {"type": "send_message", "chat_id": chat_id, "text": text}
    if markup:
        action["reply_markup"] = markup
    return action


def _answer_callback(callback_id: str, text: str | None = None) -> dict:
    action: dict[str, Any] = {"type": "answer_callback", "callback_id": callback_id}
    if text:
        action["text"] = text
    return action


def _invoice_action(
    db: Session, chat_id: int | str, order: Order, service: Service, product: ServiceProduct, lang: str
) -> dict:
    title = t(
        lang,
        "invoice_title",
        service=catalog.service_name(service, lang),
        product=catalog.product_name(product, lang),
    )
    description = catalog.product_description(product, lang) or catalog.service_description(
        service, lang
    )
    if not description:
        description = catalog.product_name(product, lang)
    return {
        "type": "invoice",
        "chat_id": chat_id,
        "lang": lang,
        "title": title,
        "description": description,
        "payload": order.id,
        "amount": order.price_stars,
        "label": catalog.product_name(product, lang),
    }


def _buy_actions(db: Session, account: Account, product_id: str, chat_id: int | str, lang: str) -> list[dict]:
    product = catalog.get_product(db, product_id)
    service = db.get(Service, product.service_id) if product else None
    if not catalog.product_available(service, product):
        return [_message(chat_id, t(lang, "product_unavailable"))]
    profile = db.get(TelegramProfile, account.id)
    try:
        order, _ = orders_service.create_or_reuse_order(
            db,
            account=account,
            service=service,
            product=product,
            telegram_user_id=profile.telegram_user_id if profile else None,
        )
    except DomainError:
        return [_message(chat_id, t(lang, "product_unavailable"))]
    return [_invoice_action(db, chat_id, order, service, product, lang)]


def _open_actions(
    db: Session, account: Account, service_id: str, chat_id: int | str, lang: str
) -> list[dict]:
    service = db.get(Service, service_id)
    if service is None or service.status != "active":
        return [_message(chat_id, t(lang, "service_unavailable"))]
    name = catalog.service_name(service, lang)
    if service.webapp_url:
        markup = {
            "inline_keyboard": [
                [{"text": t(lang, "btn_open"), "web_app": {"url": service.webapp_url}}]
            ]
        }
        return [_message(chat_id, name, markup)]
    if service.handoff_url:
        handoff = create_handoff(db, account=account, service=service)
        if handoff.get("url"):
            markup = {
                "inline_keyboard": [
                    [{"text": t(lang, "btn_open"), "url": handoff["url"]}]
                ]
            }
            return [_message(chat_id, name, markup)]
    return [_message(chat_id, t(lang, "open_failed"))]


# ---------------------------------------------------------------------------
# Command routing
# ---------------------------------------------------------------------------


def _orders_text(db: Session, account: Account, lang: str) -> str:
    orders = orders_service.list_orders(db, account.id, limit=10)
    if not orders:
        return t(lang, "orders_empty")
    lines = [t(lang, "orders_title")]
    for order in orders:
        service = db.get(Service, order.service_id)
        product = db.get(ServiceProduct, order.product_id)
        lines.append(
            t(
                lang,
                "order_line",
                order_id=order.id,
                service=catalog.service_name(service, lang) if service else order.service_id,
                product=catalog.product_name(product, lang) if product else order.product_id,
                price=order.price_stars,
                status=status_label(lang, order.status),
            )
        )
    return "\n".join(lines)


def _balance_text(db: Session, account: Account, lang: str) -> str:
    rows = balance_by_service(db, account)
    active = [(row["service"], row["balance"]) for row in rows if row["balance"] > 0]
    if not active:
        return t(lang, "balance_empty")
    lines = [t(lang, "balance_title")]
    for service, balance in active:
        lines.append(
            t(lang, "balance_line", service=catalog.service_name(service, lang), balance=balance)
        )
    return "\n".join(lines)


def _legal_text(kind: str, service: Service | None, lang: str) -> str:
    url = (service.terms_url if kind == "terms" else service.privacy_url) if service else None
    url = url or (settings.terms_url if kind == "terms" else settings.privacy_url)
    if not url:
        return t(lang, "legal_missing", contact=_support_contact(service) or "-")
    return t(lang, f"{kind}_text", url=url)


def _start_actions(
    db: Session, account: Account, payload: str, chat_id: int | str, lang: str
) -> list[dict]:
    if payload.startswith("svc_"):
        service = catalog.get_service_by_code(db, payload[4:])
        if service is None or service.status != "active":
            return [_message(chat_id, t(lang, "service_unavailable"))]
        return [
            _message(
                chat_id,
                _service_text(db, service, lang),
                _service_keyboard(db, account, service, lang),
            )
        ]
    if payload.startswith("buy_"):
        return _buy_actions(db, account, payload[4:], chat_id, lang)
    return [
        _message(chat_id, _catalog_text(db, account, lang, greeting=True), _catalog_keyboard(db, lang))
    ]


def _command_actions(
    db: Session, account: Account, text: str, chat_id: int | str, lang: str
) -> list[dict]:
    parts = text.split()
    command = parts[0][1:].split("@", 1)[0].lower() if parts else ""
    args = parts[1:]
    if command == "start":
        return _start_actions(db, account, args[0] if args else "", chat_id, lang)
    if command == "services":
        return [
            _message(
                chat_id,
                _catalog_text(db, account, lang, greeting=False),
                _catalog_keyboard(db, lang),
            )
        ]
    if command == "balance":
        return [_message(chat_id, _balance_text(db, account, lang), {"inline_keyboard": _nav_keyboard(lang)})]
    if command == "orders":
        return [_message(chat_id, _orders_text(db, account, lang), {"inline_keyboard": _nav_keyboard(lang)})]
    if command == "support":
        contact = _support_contact()
        text = t(lang, "support_text", contact=contact) if contact else t(lang, "support_missing")
        return [_message(chat_id, text)]
    if command == "terms":
        return [_message(chat_id, _legal_text("terms", None, lang))]
    if command == "privacy":
        return [_message(chat_id, _legal_text("privacy", None, lang))]
    if command == "language":
        return [_message(chat_id, t(lang, "language_title"), _language_keyboard())]
    if command == "paysupport":
        if args:
            order = db.get(Order, args[0])
            if order is None or order.account_id != account.id:
                return [_message(chat_id, t(lang, "order_not_found"))]
            return [
                _message(
                    chat_id,
                    t(
                        lang,
                        "paysupport_order",
                        order_id=order.id,
                        status=status_label(lang, order.status),
                    ),
                )
            ]
        contact = _support_contact()
        text = t(lang, "paysupport_text", contact=contact) if contact else t(lang, "support_missing")
        return [_message(chat_id, text)]
    if command in {"help", "commands"}:
        return [_message(chat_id, t(lang, "help_text"))]
    return [_message(chat_id, t(lang, "unknown_command"))]


def _route_message(db: Session, message: dict) -> list[dict]:
    tg_user = message.get("from")
    if not tg_user:
        return []
    account = get_or_create_telegram_account(db, tg_user)
    lang = account.locale
    chat_id = (message.get("chat") or {}).get("id") or tg_user.get("id")
    if chat_id is None:
        return []
    if account.is_blocked:
        return [_message(chat_id, t(lang, "blocked"))]
    text = message.get("text") or ""
    if not text.startswith("/"):
        return [_message(chat_id, t(lang, "unknown_command"))]
    return _command_actions(db, account, text, chat_id, lang)


def _route_callback(db: Session, callback: dict) -> list[dict]:
    tg_user = callback.get("from")
    if not tg_user:
        return []
    account = get_or_create_telegram_account(db, tg_user)
    lang = account.locale
    callback_id = callback.get("id") or ""
    data = str(callback.get("data") or "")
    chat_id = (callback.get("message") or {}).get("chat", {}).get("id") or tg_user.get("id")
    actions = [_answer_callback(callback_id)]
    if account.is_blocked:
        actions.append(_message(chat_id, t(lang, "blocked")))
        return actions

    if data.startswith("svc:"):
        service = db.get(Service, data[4:])
        if service is None or service.status != "active":
            actions.append(_message(chat_id, t(lang, "service_unavailable")))
        else:
            actions.append(
                _message(
                    chat_id,
                    _service_text(db, service, lang),
                    _service_keyboard(db, account, service, lang),
                )
            )
        return actions
    if data.startswith("buy:"):
        actions.extend(_buy_actions(db, account, data[4:], chat_id, lang))
        return actions
    if data.startswith("open:"):
        actions.extend(_open_actions(db, account, data[5:], chat_id, lang))
        return actions
    if data.startswith("lang:"):
        code = data[5:]
        if code in SUPPORTED_LANGUAGES:
            set_locale(db, account, code)
            actions.append(
                _message(
                    chat_id,
                    t(code, "language_saved", language=LANGUAGE_NAMES[code]),
                    {"inline_keyboard": _nav_keyboard(code)},
                )
            )
        return actions
    if data == "services":
        actions.append(
            _message(
                chat_id,
                _catalog_text(db, account, lang, greeting=False),
                _catalog_keyboard(db, lang),
            )
        )
    elif data == "orders":
        actions.append(_message(chat_id, _orders_text(db, account, lang), {"inline_keyboard": _nav_keyboard(lang)}))
    elif data == "balance":
        actions.append(_message(chat_id, _balance_text(db, account, lang), {"inline_keyboard": _nav_keyboard(lang)}))
    elif data == "support":
        contact = _support_contact()
        text = t(lang, "support_text", contact=contact) if contact else t(lang, "support_missing")
        actions.append(_message(chat_id, text))
    elif data == "language":
        actions.append(_message(chat_id, t(lang, "language_title"), _language_keyboard()))
    return actions


# ---------------------------------------------------------------------------
# Update entry points
# ---------------------------------------------------------------------------


def _register_update(db: Session, update_id: int | None) -> bool:
    if update_id is None:
        return True
    existing = db.get(BotUpdate, int(update_id))
    if existing is not None:
        metrics.inc("updates_duplicate")
        return False
    db.add(BotUpdate(update_id=int(update_id), result="processed"))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    return True


def _handle_pre_checkout(db: Session, query: dict) -> tuple[bool, str]:
    ok, error, _lang = payments.validate_pre_checkout(db, query)
    return ok, error


def _handle_payment_message(db: Session, message: dict, update_id: int | None) -> None:
    result = payments.handle_successful_payment(db, message, update_id)
    metrics.inc("webhook_processed", status=str(result.get("status")))


def _execute_actions(actions: list[dict], bot) -> None:
    for action in actions:
        kind = action.get("type")
        try:
            if kind == "send_message":
                bot.send_message(
                    action["chat_id"], action["text"], reply_markup=action.get("reply_markup")
                )
            elif kind == "invoice":
                try:
                    bot.send_invoice(
                        action["chat_id"],
                        title=action["title"],
                        description=action["description"],
                        payload=action["payload"],
                        amount=action["amount"],
                        label=action["label"],
                    )
                except TelegramError as exc:
                    log.error("sendInvoice failed: %s", exc)
                    bot.send_message(action["chat_id"], t(action.get("lang"), "invoice_failed"))
            elif kind == "answer_callback":
                bot.answer_callback_query(action["callback_id"], action.get("text"))
        except TelegramError as exc:
            log.warning("telegram action failed: %s", exc)
            metrics.inc("telegram_action_failed", action=str(kind))


def handle_update(update: dict, bot=None) -> None:
    """Process one Telegram update. Sync by design so it can run in a thread."""
    bot = bot or get_bot()
    update_id = update.get("update_id")
    if "pre_checkout_query" in update:
        query = update["pre_checkout_query"]
        try:
            with session_scope() as db:
                ok, error = _handle_pre_checkout(db, query)
        except Exception:
            metrics.inc("pre_checkout_errors")
            raise
        query_id = str(query.get("id") or "")
        try:
            bot.answer_pre_checkout(query_id, ok, error)
        except TelegramError as exc:
            metrics.inc("pre_checkout_answer_failed")
            log.error("answerPreCheckoutQuery failed: %s", exc)
            try:
                with session_scope() as db:
                    outbox.enqueue_admin_alert(
                        db,
                        key="admin_webhook_error",
                        params={"error": f"answerPreCheckoutQuery: {exc}"[:200]},
                    )
            except Exception:  # pragma: no cover - best effort
                log.exception("could not enqueue pre-checkout alert")
        return

    message = update.get("message")
    callback = update.get("callback_query")
    if message is None and callback is None:
        return

    actions: list[dict] = []
    with session_scope() as db:
        if not _register_update(db, update_id):
            return
        if message is not None:
            if "successful_payment" in message:
                _handle_payment_message(db, message, update_id)
            else:
                actions = _route_message(db, message)
        elif callback is not None:
            actions = _route_callback(db, callback)
    _execute_actions(actions, bot)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

COMMAND_KEYS = (
    "start",
    "services",
    "balance",
    "orders",
    "support",
    "language",
    "terms",
    "privacy",
    "paysupport",
    "help",
)


def register_commands(bot) -> None:
    for lang in SUPPORTED_LANGUAGES:
        commands = [
            {"command": key, "description": t(lang, f"cmd_{key}")[:256]}
            for key in COMMAND_KEYS
        ]
        bot.set_my_commands(commands, language_code=lang)
    bot.set_my_commands(
        [
            {"command": key, "description": t("en", f"cmd_{key}")[:256]}
            for key in COMMAND_KEYS
        ]
    )
    bot.set_chat_menu_button({"type": "commands"})


def setup_webhook(bot) -> None:
    bot.set_webhook(
        settings.webhook_url,
        settings.telegram_webhook_secret,
        drop_pending_updates=settings.telegram_drop_pending_updates,
    )
