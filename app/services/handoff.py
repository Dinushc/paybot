"""One-time signed handoff tokens for external services."""
from __future__ import annotations

import logging
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, HandoffToken, Service, utcnow
from app.security import SecurityError, sha256_hex, sign_payload, verify_payload
from app.services.errors import DomainError

log = logging.getLogger("app.handoff")


def _add_query(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def create_handoff(
    db: Session, *, account: Account, service: Service, ttl_seconds: int | None = None
) -> dict:
    ttl = ttl_seconds if ttl_seconds is not None else settings.handoff_ttl_seconds
    now = utcnow()
    expires_at = now + timedelta(seconds=ttl)
    nonce = secrets.token_urlsafe(16)
    token = sign_payload(
        {
            "aid": account.id,
            "sid": service.id,
            "exp": int(expires_at.timestamp()),
            "iat": int(now.timestamp()),
            "nonce": nonce,
        }
    )
    db.add(
        HandoffToken(
            token_hash=sha256_hex(token),
            account_id=account.id,
            service_id=service.id,
            expires_at=expires_at,
        )
    )
    db.flush()
    url = None
    if service.handoff_url:
        url = _add_query(service.handoff_url, "handoff", token)
    return {"token": token, "url": url, "expires_in": ttl}


def exchange_handoff(db: Session, *, token: str, service_id: str) -> dict:
    try:
        payload = verify_payload(token, max_age=None)
    except SecurityError as exc:
        raise DomainError("invalid_handoff", "handoff token is invalid", status_code=401) from exc
    if payload.get("sid") != service_id:
        raise DomainError(
            "service_mismatch", "handoff token belongs to another service", status_code=403
        )
    record = db.execute(
        select(HandoffToken)
        .where(HandoffToken.token_hash == sha256_hex(token))
        .with_for_update()
    ).scalar_one_or_none()
    if record is None:
        raise DomainError("invalid_handoff", "handoff token is invalid", status_code=401)
    if record.used_at is not None:
        log.warning("handoff token replay for service %s", service_id)
        raise DomainError("handoff_replayed", "handoff token was already used", status_code=401)
    if record.expires_at <= utcnow():
        raise DomainError("handoff_expired", "handoff token expired", status_code=401)
    if record.service_id != service_id:
        raise DomainError("service_mismatch", "handoff token belongs to another service", status_code=403)
    account = db.get(Account, record.account_id)
    if account is None or account.deleted_at is not None:
        raise DomainError("invalid_handoff", "account is not available", status_code=401)
    record.used_at = utcnow()
    db.flush()
    from app.services.catalog import get_service

    service = get_service(db, service_id)
    return {
        "account_id": account.id,
        "service_code": service.code if service else "",
        "locale": account.locale,
        "issued_at": int(payload.get("iat", 0)),
        "nonce": payload.get("nonce"),
        "server_time": int(time.time()),
    }


def handoff_url_for(service: Service, token: str) -> str | None:
    if not service.handoff_url:
        return None
    return _add_query(service.handoff_url, "handoff", token)
