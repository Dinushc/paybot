"""Audit trail helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def snapshot(obj: Any, fields: tuple[str, ...]) -> dict:
    return {field: _jsonable(getattr(obj, field, None)) for field in fields}


def write_audit(
    db: Session,
    *,
    actor: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
    actor_account_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        actor_account_id=actor_account_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    db.add(entry)
    db.flush()
    return entry


def recent(db: Session, limit: int = 50) -> list[AuditLog]:
    return list(
        db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).scalars()
    )
