"""Seed two services (photo + demo) and print service API tokens once.

Usage: ``python -m scripts.seed``
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import init_db, session_scope
from app.models import Service, ServiceApiToken, ServiceProduct, utcnow
from app.services.admin_access import create_service_token

SERVICES = [
    {
        "code": "photo",
        "name": "DVPhotoChecker",
        "description": "Автоматическая проверка фотографий на требования визы и документов.",
        "emoji": "📷",
        "status": "active",
        "webapp_url": "https://example.com/app",
        "handoff_url": None,
        "terms_url": "https://example.com/legal/terms",
        "privacy_url": "https://example.com/legal/privacy",
        "support_email": "support@example.com",
        "platform_fee_bps": 1000,
        "sort_order": 10,
        "translations": {
            "en": {
                "name": "DVPhotoChecker",
                "description": "Automated checks for visa and document photo requirements.",
            }
        },
        "products": [
            {
                "code": "solo",
                "name": "1 проверка",
                "description": "Одна проверка фотографии",
                "price_stars": 231,
                "entitlement_kind": "photo_check",
                "entitlement_quantity": 1,
                "translations": {
                    "en": {"name": "1 check", "description": "One photo check"}
                },
            },
            {
                "code": "pack5",
                "name": "5 проверок",
                "description": "Пакет из пяти проверок фотографий",
                "price_stars": 615,
                "entitlement_kind": "photo_check",
                "entitlement_quantity": 5,
                "translations": {
                    "en": {"name": "5 checks", "description": "Five photo checks"}
                },
            },
        ],
    },
    {
        "code": "demo",
        "name": "Demo Service",
        "description": "Test service used to verify that a new service can be plugged in without deploys.",
        "emoji": "🧪",
        "status": "active",
        "webapp_url": "https://example.com/demo",
        "handoff_url": None,
        "terms_url": "https://example.com/legal/terms",
        "privacy_url": "https://example.com/legal/privacy",
        "support_email": "support@example.com",
        "platform_fee_bps": 500,
        "sort_order": 20,
        "translations": {},
        "products": [
            {
                "code": "single",
                "name": "One run",
                "description": "A single paid run of the demo service",
                "price_stars": 5,
                "entitlement_kind": "demo_run",
                "entitlement_quantity": 1,
                "translations": {},
            }
        ],
    },
]


def main() -> None:
    init_db()
    issued: list[tuple[str, str]] = []
    with session_scope() as db:
        for spec in SERVICES:
            products = spec["products"]
            fields = {key: value for key, value in spec.items() if key != "products"}
            service = db.execute(
                select(Service).where(Service.code == fields["code"])
            ).scalar_one_or_none()
            if service is None:
                service = Service(**fields)
                db.add(service)
                db.flush()
                print(f"created service {service.code}")
            else:
                for key, value in fields.items():
                    setattr(service, key, value)
                service.updated_at = utcnow()
                print(f"updated service {service.code}")
            for product_spec in products:
                product = db.execute(
                    select(ServiceProduct).where(
                        ServiceProduct.service_id == service.id,
                        ServiceProduct.code == product_spec["code"],
                    )
                ).scalar_one_or_none()
                if product is None:
                    db.add(ServiceProduct(service_id=service.id, **product_spec))
                    print(f"  created product {service.code}/{product_spec['code']}")
                else:
                    for key, value in product_spec.items():
                        setattr(product, key, value)
                    print(f"  updated product {service.code}/{product_spec['code']}")
            has_token = db.execute(
                select(ServiceApiToken).where(
                    ServiceApiToken.service_id == service.id,
                    ServiceApiToken.revoked_at.is_(None),
                )
            ).first()
            if not has_token:
                _, plain = create_service_token(db, service_id=service.id)
                issued.append((service.code, plain))
    print()
    if issued:
        print("Service API tokens (shown once):")
        for code, token in issued:
            print(f"  {code}: {token}")
    else:
        print("Active service tokens already exist; no new tokens issued.")


if __name__ == "__main__":
    main()
