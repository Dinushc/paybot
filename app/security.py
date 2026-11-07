"""Tokens, signatures, webhook secrets and Telegram initData verification."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl

from app.config import settings

SIGNATURE_MAX_AGE = 24 * 60 * 60


class SecurityError(RuntimeError):
    pass


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_api_token(prefix: str = "pb") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def constant_time_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def verify_webhook_secret(value: str | None) -> bool:
    secret = settings.telegram_webhook_secret
    if not secret or not value:
        return False
    return constant_time_eq(secret, value)


def mask(value: str | None, keep: int = 6) -> str:
    """Mask a payment identifier for logs; never print the full value."""
    if not value:
        return "-"
    if len(value) <= keep:
        return "***"
    return f"{value[:keep]}…{value[-2:]}"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_payload(payload: dict, secret: str | None = None) -> str:
    """Return ``base64(json).signature`` signed with SECRET_KEY."""
    key = (secret or settings.secret_key).encode("utf-8")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(key, body, hashlib.sha256).hexdigest()
    return f"{_b64encode(body)}.{signature}"


def verify_payload(token: str, secret: str | None = None, max_age: int | None = None) -> dict:
    key = (secret or settings.secret_key).encode("utf-8")
    try:
        encoded, signature = token.split(".", 1)
        body = _b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise SecurityError("malformed token") from exc
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SecurityError("bad signature")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SecurityError("malformed payload") from exc
    if not isinstance(payload, dict):
        raise SecurityError("malformed payload")
    if max_age is not None:
        issued = int(payload.get("iat", 0))
        if issued <= 0 or time.time() - issued > max_age:
            raise SecurityError("expired token")
    expires = payload.get("exp")
    if expires is not None and int(expires) <= int(time.time()):
        raise SecurityError("expired token")
    return payload


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def init_data_replay_key(init_data: str) -> str:
    """Canonical digest for replay protection, independent of parameter order."""
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    data.pop("hash", None)
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    return sha256_hex(check_string)


def validate_init_data(
    init_data: str, bot_token: str | None = None, ttl_seconds: int | None = None
) -> dict:
    """Validate Telegram Mini App ``initData`` and return its parsed fields.

    The ``hash`` field is checked with HMAC-SHA256 over a data-check string.
    ``auth_date`` is limited so captured initData cannot be replayed forever.
    """
    token = bot_token or settings.telegram_bot_token
    if not token:
        raise SecurityError("bot token is not configured")
    if not init_data:
        raise SecurityError("empty initData")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except ValueError as exc:
        raise SecurityError("malformed initData") from exc
    data = dict(pairs)
    received = data.pop("hash", "")
    if not received:
        raise SecurityError("missing hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise SecurityError("bad initData signature")
    ttl = ttl_seconds if ttl_seconds is not None else settings.init_data_ttl_seconds
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError as exc:
        raise SecurityError("bad auth_date") from exc
    if auth_date <= 0 or auth_date > time.time() + 60 or time.time() - auth_date > ttl:
        raise SecurityError("initData expired")
    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except ValueError:
            pass
    return data
