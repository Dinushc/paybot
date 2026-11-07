"""Shared FastAPI dependencies for bearer authentication."""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.admin_access import authenticate_admin, authenticate_service
from app.services.errors import DomainError


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise DomainError("unauthorized", "missing bearer token", status_code=401)
    return token.strip()


def service_auth(scope: str):
    def dependency(request: Request, db: Session = Depends(get_db)):
        token = bearer_token(request)
        return authenticate_service(db, token, scope=scope)

    return dependency


def admin_auth(permission: str):
    def dependency(request: Request, db: Session = Depends(get_db)):
        token = bearer_token(request)
        return authenticate_admin(db, token, permission=permission)

    return dependency
