"""Minimal Telegram Bot API client.

Only the methods the platform needs are wrapped. The bot token is part of the
request URL, so it is never put into exception messages or logs.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("app.telegram")

API_BASE = "https://api.telegram.org"
CURRENCY = "XTR"
MAX_TITLE = 32
MAX_LABEL = 32
MAX_DESCRIPTION = 255
MAX_ERROR = 200
TIMEOUT = 10.0


class TelegramError(RuntimeError):
    def __init__(
        self, message: str, *, method: str = "", status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.method = method
        self.status_code = status_code

    @property
    def permanent(self) -> bool:
        """True when retrying cannot help (blocked bot, bad request, forbidden)."""
        return self.status_code in {400, 403} or "blocked" in str(self).lower()


class TelegramBot:
    def __init__(self, token: str, *, timeout: float = TIMEOUT) -> None:
        self._token = token.strip()
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _endpoint(self, method: str) -> str:
        if not self._token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not configured", method=method)
        return f"{API_BASE}/bot{self._token}/{method}"

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = httpx.post(
                self._endpoint(method), json=payload, timeout=self._timeout
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            # Never interpolate the URL: it contains the token.
            raise TelegramError(
                f"Telegram API {method} is unavailable", method=method
            ) from None
        if not isinstance(data, dict):
            raise TelegramError(f"Telegram API {method}: invalid response", method=method)
        if not data.get("ok"):
            description = str(data.get("description") or "unknown error")[:MAX_ERROR]
            raise TelegramError(
                f"Telegram API {method}: {description}",
                method=method,
                status_code=response.status_code,
            )
        return data.get("result")

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict | None = None,
        disable_preview: bool = True,
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": disable_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    def send_invoice(
        self,
        chat_id: int | str,
        *,
        title: str,
        description: str,
        payload: str,
        amount: int,
        label: str,
        start_parameter: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "title": title[:MAX_TITLE],
            "description": description[:MAX_DESCRIPTION],
            "payload": payload[:128],
            "provider_token": "",
            "currency": CURRENCY,
            "prices": [{"label": label[:MAX_LABEL], "amount": int(amount)}],
        }
        if start_parameter:
            body["start_parameter"] = start_parameter[:64]
        return self.call("sendInvoice", body)

    def create_invoice_link(
        self,
        *,
        title: str,
        description: str,
        payload: str,
        amount: int,
        label: str,
    ) -> str:
        result = self.call(
            "createInvoiceLink",
            {
                "title": title[:MAX_TITLE],
                "description": description[:MAX_DESCRIPTION],
                "payload": payload[:128],
                "provider_token": "",
                "currency": CURRENCY,
                "prices": [{"label": label[:MAX_LABEL], "amount": int(amount)}],
            },
        )
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return str(result.get("url") or "")
        return ""

    def answer_pre_checkout(
        self, query_id: str, ok: bool, error_message: str | None = None
    ) -> None:
        payload: dict[str, Any] = {"pre_checkout_query_id": query_id, "ok": ok}
        if not ok:
            payload["error_message"] = (error_message or "Payment rejected")[:MAX_ERROR]
        self.call("answerPreCheckoutQuery", payload)

    def answer_callback_query(self, callback_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:200]
        self.call("answerCallbackQuery", payload)

    def refund_star_payment(self, telegram_user_id: int | str, charge_id: str) -> None:
        if not charge_id:
            raise TelegramError("missing telegram_payment_charge_id", method="refundStarPayment")
        try:
            user_id: int | str = int(telegram_user_id)
        except (TypeError, ValueError):
            user_id = telegram_user_id
        self.call(
            "refundStarPayment",
            {"user_id": user_id, "telegram_payment_charge_id": charge_id},
        )

    def set_my_commands(
        self, commands: list[dict[str, str]], language_code: str | None = None
    ) -> None:
        payload: dict[str, Any] = {"commands": commands}
        if language_code:
            payload["language_code"] = language_code
        self.call("setMyCommands", payload)

    def set_chat_menu_button(self, menu_button: dict | None = None) -> None:
        self.call(
            "setChatMenuButton",
            {"menu_button": menu_button or {"type": "commands"}},
        )

    def set_webhook(
        self, url: str, secret_token: str, *, drop_pending_updates: bool = False
    ) -> Any:
        return self.call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "drop_pending_updates": drop_pending_updates,
                "max_connections": 40,
            },
        )

    def get_webhook_info(self) -> dict:
        result = self.call("getWebhookInfo", {})
        return result if isinstance(result, dict) else {}

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> Any:
        return self.call(
            "deleteWebhook", {"drop_pending_updates": drop_pending_updates}
        )


_bot: TelegramBot | None = None


def get_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        _bot = TelegramBot(settings.telegram_bot_token)
    return _bot


def set_bot(bot: TelegramBot | None) -> None:
    global _bot
    _bot = bot


def reset_bot() -> None:
    global _bot
    _bot = None
