"""Service and admin API token authentication plus RBAC."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminApiKey, Service, ServiceApiToken, utcnow
from app.security import new_api_token, sha256_hex
from app.services.errors import DomainError

log = logging.getLogger("app.access")

ROLES = {"platform_admin", "service_admin", "support", "finance"}

SERVICE_SCOPES = {
    "handoff",
    "entitlements:read",
    "entitlements:consume",
    "orders:read",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "platform_admin": {
        "services:read",
        "services:write",
        "products:read",
        "products:write",
        "orders:read",
        "orders:refund",
        "entitlements:read",
        "entitlements:adjust",
        "users:read",
        "users:anonymize",
        "audit:read",
        "reports:read",
        "tokens:write",
        "keys:write",
        "payments:read",
    },
    "service_admin": {
        "services:read",
        "services:write",
        "products:read",
        "products:write",
        "orders:read",
        "orders:refund",
        "entitlements:read",
        "reports:read",
    },
    "support": {
        "services:read",
        "products:read",
        "orders:read",
        "orders:refund",
        "entitlements:read",
        "users:read",
        "users:anonymize",
        "audit:read",
    },
    "finance": {
        "services:read",
        "products:read",
        "orders:read",
        "payments:read",
        "reports:read",
    },
}


def permissions_for(role: str) -> set[str]:
    return ROLE_PERMISSIONS.get(role, set())


def can(role: str, permission: str) -> bool:
    return permission in permissions_for(role)


def create_service_token(
    db: Session, *, service_id: str, scopes: list[str] | None = None
) -> tuple[ServiceApiToken, str]:
    chosen = sorted(set(scopes or SERVICE_SCOPES))
    unknown = set(chosen) - SERVICE_SCOPES
    if unknown:
        raise DomainError("invalid_scope", f"unknown scopes: {', '.join(sorted(unknown))}")
    token = new_api_token("svc")
    record = ServiceApiToken(
        service_id=service_id,
        token_hash=sha256_hex(token),
        scopes=chosen,
    )
    db.add(record)
    db.flush()
    return record, token


def tokens_for_service(db: Session, service_id: str) -> list[ServiceApiToken]:
    return list(
        db.execute(
            select(ServiceApiToken)
            .where(ServiceApiToken.service_id == service_id)
            .order_by(ServiceApiToken.created_at.desc())
        ).scalars()
    )


def revoke_service_token(db: Session, token_id: str) -> ServiceApiToken:
    record = db.get(ServiceApiToken, token_id)
    if record is None:
        raise DomainError("token_not_found", "service token not found", status_code=404)
    record.revoked_at = utcnow()
    db.flush()
    return record


def authenticate_service(
    db: Session, token: str, *, scope: str
) -> tuple[ServiceApiToken, Service]:
    if not token:
        raise DomainError("unauthorized", "missing bearer token", status_code=401)
    record = db.execute(
        select(ServiceApiToken).where(
            ServiceApiToken.token_hash == sha256_hex(token),
            ServiceApiToken.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if record is None:
        raise DomainError("unauthorized", "invalid service token", status_code=401)
    if scope not in (record.scopes or []):
        raise DomainError("forbidden", "token does not have the required scope", status_code=403)
    service = db.get(Service, record.service_id)
    if service is None:
        raise DomainError("unauthorized", "service no longer exists", status_code=401)
    if service.status != "active":
        raise DomainError("service_disabled", "service is disabled", status_code=403)
    return record, service


def create_admin_key(
    db: Session,
    *,
    name: str,
    role: str,
    service_id: str | None = None,
    token: str | None = None,
) -> tuple[AdminApiKey, str]:
    if role not in ROLES:
        raise DomainError("invalid_role", "unknown role")
    if role == "service_admin" and not service_id:
        raise DomainError("invalid_service", "service_admin requires service_id")
    plain = token or new_api_token("adm")
    record = AdminApiKey(
        name=name[:128] or "admin",
        token_hash=sha256_hex(plain),
        role=role,
        service_id=service_id,
    )
    db.add(record)
    db.flush()
    return record, plain


def authenticate_admin(db: Session, token: str, *, permission: str) -> AdminApiKey:
    if not token:
        raise DomainError("unauthorized", "missing bearer token", status_code=401)
    record = db.execute(
        select(AdminApiKey).where(
            AdminApiKey.token_hash == sha256_hex(token),
            AdminApiKey.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if record is None:
        raise DomainError("unauthorized", "invalid admin token", status_code=401)
    if not can(record.role, permission):
        raise DomainError("forbidden", "insufficient permissions", status_code=403)
    return record


def ensure_bootstrap_admin(db: Session) -> None:
    """Keep the env-provided admin token usable without storing it in plain text."""
    token = settings.admin_api_token
    if not token:
        return
    token_hash = sha256_hex(token)
    record = db.execute(
        select(AdminApiKey).where(AdminApiKey.name == "bootstrap")
    ).scalar_one_or_none()
    if record is None:
        db.add(
            AdminApiKey(
                name="bootstrap",
                token_hash=token_hash,
                role="platform_admin",
            )
        )
    elif record.token_hash != token_hash:
        record.token_hash = token_hash
        record.revoked_at = None
    db.flush()


def token_expiry_hint() -> timedelta:  # pragma: no cover - documentation helper
    return timedelta(days=90)
