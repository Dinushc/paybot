from __future__ import annotations

import time

import pytest

from app.i18n import localized, normalize_lang, status_label, t
from app.security import (
    SecurityError,
    mask,
    sha256_hex,
    sign_payload,
    verify_payload,
    verify_webhook_secret,
)


def test_normalize_and_fallback():
    assert normalize_lang("ru-RU") == "ru"
    assert normalize_lang("de") == "en"
    assert normalize_lang(None) == "en"
    assert normalize_lang("sah") == "sah"
    assert normalize_lang("tt") == "ru"
    assert t("ru", "btn_open") == "Открыть сервис"
    assert t("de", "btn_open") == "Open service"
    assert t("de", "missing_key") == "missing_key"
    assert status_label("ru", "paid") == "оплачен"
    assert status_label("en", "custom") == "custom"


def test_localized_service_fields():
    translations = {"ru": {"name": "Фото", "description": "Описание"}}
    assert localized(translations, "ru", "name", "Photo") == "Фото"
    assert localized(translations, "en", "name", "Photo") == "Photo"
    assert localized(translations, "ru", "empty", "fallback") == "fallback"
    assert localized(None, "ru", "name", "Photo") == "Photo"


def test_signed_payloads():
    token = sign_payload({"kid": "abc", "exp": int(time.time()) + 60})
    assert verify_payload(token)["kid"] == "abc"
    with pytest.raises(SecurityError):
        verify_payload(token + "x")
    expired = sign_payload({"kid": "abc", "exp": int(time.time()) - 1})
    with pytest.raises(SecurityError):
        verify_payload(expired)
    old = sign_payload({"kid": "abc", "iat": int(time.time()) - 100})
    with pytest.raises(SecurityError):
        verify_payload(old, max_age=10)


def test_mask_and_hash():
    assert mask("abcdefghijkl") == "abcdef…kl"
    assert mask("") == "-"
    assert len(sha256_hex("x")) == 64
    assert verify_webhook_secret("test-webhook-secret")
    assert not verify_webhook_secret("nope")
