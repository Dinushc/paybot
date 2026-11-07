"""Server-rendered admin UI (sessions, CSRF, RBAC)."""
from __future__ import annotations

import csv
import hmac
import io
import logging
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.ratelimit import SlidingWindowLimiter
from app.models import (
    Account,
    AdminApiKey,
    AuditLog,
    Entitlement,
    Order,
    PaymentEvent,
    Service,
    ServiceProduct,
    TelegramProfile,
)
from app.security import new_csrf_token, sha256_hex, sign_payload, verify_payload
from app.services import audit, payments, reports
from app.services.admin_access import (
    can,
    create_service_token,
    tokens_for_service,
)
from app.services.errors import DomainError
from app.api.admin import _product_dict, _service_dict, _validate_product, _validate_service

log = logging.getLogger("app.admin_ui")

router = APIRouter(prefix="/admin", tags=["admin-ui"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.globals["can"] = can
COOKIE = "paybot_admin"
SESSION_HOURS = 12
_login_limiter = SlidingWindowLimiter(20, window_seconds=60)


def _secure() -> bool:
    return settings.public_base_url.startswith("https://")


def _load_session(request: Request, db: Session) -> dict | None:
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return None
    try:
        payload = verify_payload(cookie)
    except Exception:
        return None
    record = db.get(AdminApiKey, str(payload.get("kid") or ""))
    if record is None or record.revoked_at is not None or record.role != payload.get("role"):
        return None
    return {
        "id": record.id,
        "role": record.role,
        "name": record.name,
        "service_id": record.service_id,
        "csrf": payload.get("csrf"),
        "actor": f"{record.role}:{record.name}:{record.id}",
    }


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _require(
    request: Request, db: Session, permission: str
) -> tuple[dict | None, RedirectResponse | None]:
    session = _load_session(request, db)
    if session is None:
        return None, _login_redirect()
    if not can(session["role"], permission):
        raise HTTPException(status_code=403, detail="insufficient permissions")
    return session, None


def _check_csrf(request: Request, session: dict, form) -> None:
    value = str(form.get("csrf") or "")
    expected = str(session.get("csrf") or "")
    if not expected or not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def _redirect(path: str, *, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    if notice:
        path = f"{path}{'&' if '?' in path else '?'}notice={quote(notice)}"
    if error:
        path = f"{path}{'&' if '?' in path else '?'}error={quote(error)}"
    return RedirectResponse(path, status_code=303)


def _render(request: Request, name: str, context: dict, *, status_code: int = 200) -> HTMLResponse:
    base = {
        "request": request,
        "session": context.get("session"),
        "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error"),
    }
    base.update(context)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def _form_str(form, key: str, default: str = "") -> str:
    value = form.get(key)
    return str(value).strip() if value is not None else default


def _form_opt(form, key: str) -> str | None:
    value = _form_str(form, key)
    return value or None


def _form_int(form, key: str, default: int = 0) -> int:
    try:
        return int(str(form.get(key) or default))
    except (TypeError, ValueError):
        return default


def _form_bool(form, key: str) -> bool:
    return str(form.get(key) or "").lower() in {"on", "1", "true", "yes"}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if _load_session(request, db):
        return RedirectResponse("/admin/services", status_code=303)
    return _render(request, "admin/login.html", {"session": None})


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not _login_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="too many login attempts")
    form = await request.form()
    token = str(form.get("token") or "").strip()
    record = db.execute(
        select(AdminApiKey).where(
            AdminApiKey.token_hash == sha256_hex(token),
            AdminApiKey.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if record is None:
        return _render(
            request,
            "admin/login.html",
            {"session": None, "error": "Invalid admin token"},
            status_code=401,
        )
    now = int(time.time())
    payload = {
        "kid": record.id,
        "role": record.role,
        "name": record.name,
        "csrf": new_csrf_token(),
        "iat": now,
        "exp": now + SESSION_HOURS * 3600,
    }
    response = RedirectResponse("/admin/services", status_code=303)
    response.set_cookie(
        COOKIE,
        sign_payload(payload),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/admin",
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(COOKIE, path="/admin")
    return response


@router.get("", include_in_schema=False)
def admin_root():
    return RedirectResponse("/admin/services", status_code=303)


@router.get("/services", response_class=HTMLResponse)
def services_page(request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "services:read")
    if redirect:
        return redirect
    query = select(Service).order_by(Service.sort_order, Service.name)
    if session["role"] == "service_admin":
        query = query.where(Service.id == session["service_id"])
    services = list(db.execute(query).scalars())
    counts = {
        service.id: db.query(ServiceProduct)
        .filter(ServiceProduct.service_id == service.id)
        .count()
        for service in services
    }
    return _render(
        request,
        "admin/services.html",
        {"session": session, "services": services, "counts": counts},
    )


@router.post("/services")
async def create_service(request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "services:write")
    if redirect:
        return redirect
    if session["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="only platform admins can create services")
    form = await request.form()
    _check_csrf(request, session, form)
    service = Service(
        code=_form_str(form, "code"),
        name=_form_str(form, "name"),
        description=_form_str(form, "description"),
        emoji=_form_str(form, "emoji") or None,
        status=_form_str(form, "status", "draft"),
        webapp_url=_form_opt(form, "webapp_url"),
        handoff_url=_form_opt(form, "handoff_url"),
        events_url=_form_opt(form, "events_url"),
        events_secret=_form_opt(form, "events_secret"),
        terms_url=_form_opt(form, "terms_url"),
        privacy_url=_form_opt(form, "privacy_url"),
        support_email=_form_opt(form, "support_email"),
        support_url=_form_opt(form, "support_url"),
        platform_fee_bps=_form_int(form, "platform_fee_bps"),
        sort_order=_form_int(form, "sort_order"),
    )
    try:
        if db.execute(select(Service).where(Service.code == service.code)).scalar_one_or_none():
            raise DomainError("duplicate_code", "service code is already in use", status_code=409)
        _validate_service(service)
        db.add(service)
        db.flush()
        audit.write_audit(
            db,
            actor=session["actor"],
            action="service.create",
            entity_type="service",
            entity_id=service.id,
            after=_service_dict(service),
        )
    except DomainError as exc:
        db.rollback()
        return _redirect("/admin/services", error=exc.message)
    return _redirect(f"/admin/services/{service.id}", notice="Service created")


@router.get("/services/{service_id}", response_class=HTMLResponse)
def service_page(service_id: str, request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "services:read")
    if redirect:
        return redirect
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="service not found")
    if session["role"] == "service_admin" and session["service_id"] != service.id:
        raise HTTPException(status_code=403, detail="service scope mismatch")
    products = list(
        db.execute(
            select(ServiceProduct)
            .where(ServiceProduct.service_id == service.id)
            .order_by(ServiceProduct.sort_order, ServiceProduct.price_stars)
        ).scalars()
    )
    tokens = tokens_for_service(db, service.id) if session["role"] == "platform_admin" else []
    return _render(
        request,
        "admin/service.html",
        {
            "session": session,
            "service": service,
            "products": products,
            "tokens": tokens,
            "new_token": None,
        },
    )


@router.post("/services/{service_id}")
async def update_service_ui(service_id: str, request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "services:write")
    if redirect:
        return redirect
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="service not found")
    if session["role"] == "service_admin" and session["service_id"] != service.id:
        raise HTTPException(status_code=403, detail="service scope mismatch")
    form = await request.form()
    _check_csrf(request, session, form)
    before = _service_dict(service)
    service.name = _form_str(form, "name", service.name)
    service.description = _form_str(form, "description", service.description)
    service.emoji = _form_str(form, "emoji") or None
    service.status = _form_str(form, "status", service.status)
    service.webapp_url = _form_opt(form, "webapp_url")
    service.handoff_url = _form_opt(form, "handoff_url")
    service.events_url = _form_opt(form, "events_url")
    service.events_secret = _form_opt(form, "events_secret")
    service.terms_url = _form_opt(form, "terms_url")
    service.privacy_url = _form_opt(form, "privacy_url")
    service.support_email = _form_opt(form, "support_email")
    service.support_url = _form_opt(form, "support_url")
    service.platform_fee_bps = _form_int(form, "platform_fee_bps", service.platform_fee_bps)
    service.sort_order = _form_int(form, "sort_order", service.sort_order)
    try:
        _validate_service(service)
        db.flush()
        audit.write_audit(
            db,
            actor=session["actor"],
            action="service.update",
            entity_type="service",
            entity_id=service.id,
            before=before,
            after=_service_dict(service),
        )
    except DomainError as exc:
        db.rollback()
        return _redirect(f"/admin/services/{service_id}", error=exc.message)
    return _redirect(f"/admin/services/{service_id}", notice="Service updated")


@router.post("/services/{service_id}/products")
async def create_product_ui(service_id: str, request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "products:write")
    if redirect:
        return redirect
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="service not found")
    if session["role"] == "service_admin" and session["service_id"] != service.id:
        raise HTTPException(status_code=403, detail="service scope mismatch")
    form = await request.form()
    _check_csrf(request, session, form)
    product = ServiceProduct(
        service_id=service.id,
        code=_form_str(form, "code"),
        name=_form_str(form, "name"),
        description=_form_str(form, "description"),
        price_stars=_form_int(form, "price_stars"),
        entitlement_kind=_form_str(form, "entitlement_kind"),
        entitlement_quantity=_form_int(form, "entitlement_quantity", 1),
        entitlement_ttl_days=(
            _form_int(form, "entitlement_ttl_days") or None
            if _form_str(form, "entitlement_ttl_days")
            else None
        ),
        active=True,
        sort_order=_form_int(form, "sort_order"),
    )
    try:
        if db.execute(
            select(ServiceProduct).where(
                ServiceProduct.service_id == service.id, ServiceProduct.code == product.code
            )
        ).scalar_one_or_none():
            raise DomainError("duplicate_code", "product code is already in use", status_code=409)
        _validate_product(product)
        db.add(product)
        db.flush()
        audit.write_audit(
            db,
            actor=session["actor"],
            action="product.create",
            entity_type="product",
            entity_id=product.id,
            after=_product_dict(product),
        )
    except DomainError as exc:
        db.rollback()
        return _redirect(f"/admin/services/{service_id}", error=exc.message)
    return _redirect(f"/admin/services/{service_id}", notice="Product created")


@router.post("/services/{service_id}/products/{product_id}")
async def update_product_ui(
    service_id: str, product_id: str, request: Request, db: Session = Depends(get_db)
):
    session, redirect = _require(request, db, "products:write")
    if redirect:
        return redirect
    if session["role"] == "service_admin" and session["service_id"] != service_id:
        raise HTTPException(status_code=403, detail="service scope mismatch")
    product = db.get(ServiceProduct, product_id)
    if product is None or product.service_id != service_id:
        raise HTTPException(status_code=404, detail="product not found")
    form = await request.form()
    _check_csrf(request, session, form)
    before = _product_dict(product)
    product.name = _form_str(form, "name", product.name)
    product.description = _form_str(form, "description", product.description)
    product.price_stars = _form_int(form, "price_stars", product.price_stars)
    product.entitlement_kind = _form_str(form, "entitlement_kind", product.entitlement_kind)
    product.entitlement_quantity = _form_int(
        form, "entitlement_quantity", product.entitlement_quantity
    )
    product.entitlement_ttl_days = (
        _form_int(form, "entitlement_ttl_days") or None
        if _form_str(form, "entitlement_ttl_days")
        else None
    )
    product.active = _form_bool(form, "active")
    product.sort_order = _form_int(form, "sort_order", product.sort_order)
    try:
        _validate_product(product)
        db.flush()
        audit.write_audit(
            db,
            actor=session["actor"],
            action="product.update",
            entity_type="product",
            entity_id=product.id,
            before=before,
            after=_product_dict(product),
        )
    except DomainError as exc:
        db.rollback()
        return _redirect(f"/admin/services/{service_id}", error=exc.message)
    return _redirect(f"/admin/services/{service_id}", notice="Product updated")


@router.post("/services/{service_id}/tokens")
async def issue_token_ui(service_id: str, request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "tokens:write")
    if redirect:
        return redirect
    if session["role"] != "platform_admin":
        raise HTTPException(status_code=403, detail="only platform admins can issue tokens")
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="service not found")
    form = await request.form()
    _check_csrf(request, session, form)
    record, token = create_service_token(db, service_id=service.id)
    audit.write_audit(
        db,
        actor=session["actor"],
        action="service_token.create",
        entity_type="service_token",
        entity_id=record.id,
        after={"service_id": service.id, "scopes": record.scopes},
    )
    products = list(
        db.execute(
            select(ServiceProduct)
            .where(ServiceProduct.service_id == service.id)
            .order_by(ServiceProduct.sort_order, ServiceProduct.price_stars)
        ).scalars()
    )
    return _render(
        request,
        "admin/service.html",
        {
            "session": session,
            "service": service,
            "products": products,
            "tokens": tokens_for_service(db, service.id),
            "new_token": token,
        },
    )


@router.get("/orders", response_class=HTMLResponse)
def orders_page(
    request: Request,
    status: str | None = None,
    service_id: str | None = None,
    db: Session = Depends(get_db),
):
    session, redirect = _require(request, db, "orders:read")
    if redirect:
        return redirect
    query = select(Order).order_by(Order.created_at.desc()).limit(200)
    if session["role"] == "service_admin":
        query = query.where(Order.service_id == session["service_id"])
    if status:
        query = query.where(Order.status == status)
    if service_id:
        query = query.where(Order.service_id == service_id)
    orders = list(db.execute(query).scalars())
    services = {
        service.id: service
        for service in db.execute(select(Service)).scalars()
    }
    products = {
        product.id: product
        for product in db.execute(select(ServiceProduct)).scalars()
    }
    return _render(
        request,
        "admin/orders.html",
        {
            "session": session,
            "orders": orders,
            "services": services,
            "products": products,
            "status": status or "",
        },
    )


@router.post("/orders/{order_id}/refund")
async def refund_order_ui(order_id: str, request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "orders:refund")
    if redirect:
        return redirect
    form = await request.form()
    _check_csrf(request, session, form)
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if session["role"] == "service_admin" and order.service_id != session["service_id"]:
        raise HTTPException(status_code=403, detail="service scope mismatch")
    from app.telegram import get_bot

    try:
        payments.refund_order(db, order_id=order_id, bot=get_bot(), actor=session["actor"])
    except DomainError as exc:
        db.rollback()
        return _redirect("/admin/orders", error=exc.message)
    return _redirect("/admin/orders", notice="Refund completed")


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request, query: str | None = None, db: Session = Depends(get_db)
):
    session, redirect = _require(request, db, "users:read")
    if redirect:
        return redirect
    rows = []
    if query and query.strip():
        like = f"%{query.strip()}%"
        statement = (
            select(Account, TelegramProfile)
            .outerjoin(TelegramProfile, TelegramProfile.account_id == Account.id)
            .where(
                (Account.email.ilike(like))
                | (Account.name.ilike(like))
                | (TelegramProfile.username.ilike(like))
                | (
                    TelegramProfile.telegram_user_id
                    == (int(query) if query.strip().isdigit() else -1)
                )
            )
            .limit(100)
        )
        rows = list(db.execute(statement))
    return _render(
        request,
        "admin/users.html",
        {"session": session, "rows": rows, "query": query or ""},
    )


@router.get("/entitlements", response_class=HTMLResponse)
def entitlements_page(
    request: Request,
    account_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    session, redirect = _require(request, db, "entitlements:read")
    if redirect:
        return redirect
    query = select(Entitlement).order_by(Entitlement.created_at.desc()).limit(200)
    if session["role"] == "service_admin":
        query = query.where(Entitlement.service_id == session["service_id"])
    if account_id:
        query = query.where(Entitlement.account_id == account_id)
    if kind:
        query = query.where(Entitlement.kind == kind)
    if status:
        query = query.where(Entitlement.status == status)
    entitlements = list(db.execute(query).scalars())
    services = {item.id: item for item in db.execute(select(Service)).scalars()}
    return _render(
        request,
        "admin/entitlements.html",
        {
            "session": session,
            "entitlements": entitlements,
            "services": services,
            "account_id": account_id or "",
            "kind": kind or "",
            "status": status or "",
        },
    )


@router.get("/payments", response_class=HTMLResponse)
def payments_page(request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "payments:read")
    if redirect:
        return redirect
    events = list(
        db.execute(
            select(PaymentEvent).order_by(PaymentEvent.created_at.desc()).limit(200)
        ).scalars()
    )
    return _render(
        request, "admin/payments.html", {"session": session, "events": events}
    )


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "audit:read")
    if redirect:
        return redirect
    entries = list(
        db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).scalars()
    )
    return _render(request, "admin/audit.html", {"session": session, "entries": entries})


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "reports:read")
    if redirect:
        return redirect
    service_id = session["service_id"] if session["role"] == "service_admin" else None
    report = reports.revenue(db, service_id=service_id)
    return _render(request, "admin/reports.html", {"session": session, "report": report})


@router.get("/reports.csv")
def reports_csv(request: Request, db: Session = Depends(get_db)):
    session, redirect = _require(request, db, "reports:read")
    if redirect:
        return redirect
    service_id = session["service_id"] if session["role"] == "service_admin" else None
    report = reports.revenue(db, service_id=service_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "service_code",
            "service_name",
            "orders",
            "gross_stars",
            "platform_fee_stars",
            "service_share_stars",
            "refund_orders",
            "refunded_stars",
        ]
    )
    for row in report["rows"]:
        writer.writerow(
            [
                row["service_code"],
                row["service_name"],
                row["orders"],
                row["gross_stars"],
                row["platform_fee_stars"],
                row["service_share_stars"],
                row["refund_orders"],
                row["refunded_stars"],
            ]
        )
    return Response(buffer.getvalue(), media_type="text/csv")
