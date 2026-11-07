"""Service catalog queries and localization."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.i18n import localized
from app.models import Entitlement, Service, ServiceProduct, utcnow


def active_services(db: Session) -> list[Service]:
    return list(
        db.execute(
            select(Service)
            .where(Service.status == "active")
            .order_by(Service.sort_order, Service.name)
        ).scalars()
    )


def get_service(db: Session, service_id: str) -> Service | None:
    return db.get(Service, service_id)


def get_service_by_code(db: Session, code: str) -> Service | None:
    return db.execute(select(Service).where(Service.code == code)).scalar_one_or_none()


def active_products(db: Session, service_id: str) -> list[ServiceProduct]:
    return list(
        db.execute(
            select(ServiceProduct)
            .where(
                ServiceProduct.service_id == service_id,
                ServiceProduct.active.is_(True),
            )
            .order_by(ServiceProduct.sort_order, ServiceProduct.price_stars)
        ).scalars()
    )


def get_product(db: Session, product_id: str) -> ServiceProduct | None:
    return db.get(ServiceProduct, product_id)


def product_available(service: Service | None, product: ServiceProduct | None) -> bool:
    return bool(
        service
        and product
        and service.status == "active"
        and product.active
        and product.service_id == service.id
    )


def service_name(service: Service, lang: str) -> str:
    return localized(service.translations, lang, "name", service.name)


def service_description(service: Service, lang: str) -> str:
    return localized(service.translations, lang, "description", service.description)


def product_name(product: ServiceProduct, lang: str) -> str:
    return localized(product.translations, lang, "name", product.name)


def product_description(product: ServiceProduct, lang: str) -> str:
    return localized(product.translations, lang, "description", product.description)


def service_balance(db: Session, account_id: str, service_id: str) -> int:
    value = db.execute(
        select(func.coalesce(func.sum(Entitlement.remaining), 0)).where(
            Entitlement.account_id == account_id,
            Entitlement.service_id == service_id,
            Entitlement.status == "active",
            (Entitlement.expires_at.is_(None)) | (Entitlement.expires_at > utcnow()),
        )
    ).scalar_one()
    return int(value or 0)


def has_access(db: Session, account_id: str, service_id: str) -> bool:
    return service_balance(db, account_id, service_id) > 0
