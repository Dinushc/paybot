from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp(prefix='paybot-tests-')) / 'test.db'}"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789abcdef-0123456789"
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8000"
os.environ["ENVIRONMENT"] = "development"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["TELEGRAM_AUTO_SETUP"] = "0"
os.environ["ADMIN_API_TOKEN"] = "test-admin-token-0123456789"
os.environ["ADMIN_CHAT_ID"] = "999"
os.environ["SUPPORT_EMAIL"] = "support@example.com"
os.environ["AUTO_CREATE_TABLES"] = "0"
os.environ["WORKERS_ENABLED"] = "0"
os.environ["METRICS_ENABLED"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import metrics  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.services.admin_access import ensure_bootstrap_admin  # noqa: E402
from app.telegram import reset_bot, set_bot  # noqa: E402

WEBHOOK_PATH = "/api/webhooks/telegram"
WEBHOOK_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret}
ADMIN_HEADERS = {"Authorization": f"Bearer {settings.admin_api_token}"}


class FakeBot:
    def __init__(self) -> None:
        self.configured = True
        self.calls: list[tuple] = []
        self.refund_error: Exception | None = None
        self.send_error: Exception | None = None
        self.webhook_info = {"last_error_message": "", "pending_update_count": 0}

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def send_message(self, chat_id, text, reply_markup=None, disable_preview=True):
        if self.send_error is not None:
            raise self.send_error
        self._record("send_message", chat_id, text, reply_markup=reply_markup)
        return {"message_id": 1}

    def send_invoice(self, chat_id, *, title, description, payload, amount, label, start_parameter=None):
        self._record(
            "send_invoice",
            chat_id,
            title=title,
            description=description,
            payload=payload,
            amount=amount,
            label=label,
        )
        return {"message_id": 2}

    def create_invoice_link(self, **kwargs):
        self._record("create_invoice_link", **kwargs)
        return "https://t.me/invoice/test"

    def answer_pre_checkout(self, query_id, ok, error_message=None):
        self._record("answer_pre_checkout", query_id, ok, error_message)

    def answer_callback_query(self, callback_id, text=None):
        self._record("answer_callback_query", callback_id, text)

    def refund_star_payment(self, telegram_user_id, charge_id):
        if self.refund_error is not None:
            raise self.refund_error
        self._record("refund_star_payment", telegram_user_id, charge_id)

    def set_my_commands(self, commands, language_code=None):
        self._record("set_my_commands", commands, language_code)

    def set_chat_menu_button(self, menu_button=None):
        self._record("set_chat_menu_button", menu_button)

    def set_webhook(self, url, secret_token, *, drop_pending_updates=False):
        self._record("set_webhook", url, secret_token, drop_pending_updates)

    def get_webhook_info(self):
        self._record("get_webhook_info")
        return self.webhook_info

    def delete_webhook(self, *, drop_pending_updates=False):
        self._record("delete_webhook", drop_pending_updates)

    def calls_named(self, name: str) -> list[tuple]:
        return [call for call in self.calls if call[0] == name]


@pytest.fixture()
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    metrics.reset()
    with session_scope() as db:
        ensure_bootstrap_admin(db)
    yield


@pytest.fixture()
def db(fresh_db):
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def fake_bot(fresh_db):
    bot = FakeBot()
    set_bot(bot)
    yield bot
    reset_bot()


@pytest.fixture()
def client(fresh_db, fake_bot):
    with TestClient(app) as test_client:
        yield test_client
