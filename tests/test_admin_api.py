from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import AuditLog
from app.services.admin_access import create_admin_key
from tests.conftest import ADMIN_HEADERS
from tests.helpers import buy_and_pay, make_catalog


def _service_payload(code: str = "new_svc", status: str = "active") -> dict:
    return {
        "code": code,
        "name": "New Service",
        "description": "A new service",
        "status": status,
        "webapp_url": "https://example.com/new",
        "terms_url": "https://example.com/terms",
        "privacy_url": "https://example.com/privacy",
        "support_email": "support@example.com",
        "platform_fee_bps": 750,
    }


def _role_headers(role: str, service_id: str | None = None) -> dict:
    with session_scope() as db:
        _, token = create_admin_key(db, name=role, role=role, service_id=service_id)
    return {"Authorization": f"Bearer {token}"}


def test_admin_auth_and_validation(client):
    assert client.get("/api/admin/services").status_code == 401
    assert (
        client.get("/api/admin/services", headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )

    resp = client.post("/api/admin/services", json=_service_payload(), headers=ADMIN_HEADERS)
    assert resp.status_code == 201, resp.text
    service_id = resp.json()["id"]

    resp = client.post("/api/admin/services", json=_service_payload(), headers=ADMIN_HEADERS)
    assert resp.status_code == 409

    bad = _service_payload("bad_svc")
    bad["terms_url"] = None
    resp = client.post("/api/admin/services", json=bad, headers=ADMIN_HEADERS)
    assert resp.status_code == 422

    bad = _service_payload("bad_url")
    bad["webapp_url"] = "http://insecure.example"
    resp = client.post("/api/admin/services", json=bad, headers=ADMIN_HEADERS)
    assert resp.status_code == 422

    resp = client.post(
        f"/api/admin/services/{service_id}/products",
        json={
            "code": "p1",
            "name": "Product",
            "price_stars": 10,
            "entitlement_kind": "demo",
            "entitlement_quantity": 1,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        f"/api/admin/services/{service_id}/products",
        json={
            "code": "p2",
            "name": "Cheap",
            "price_stars": 0,
            "entitlement_kind": "demo",
            "entitlement_quantity": 1,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422

    with session_scope() as db:
        actions = {entry.action for entry in db.execute(select(AuditLog)).scalars()}
        assert "service.create" in actions
        assert "product.create" in actions


def test_rbac_roles_and_service_scope(client):
    catalog = make_catalog()
    support = _role_headers("support")
    finance = _role_headers("finance")
    service_admin = _role_headers("service_admin", catalog["service_id"])

    resp = client.post("/api/admin/services", json=_service_payload("rbac"), headers=support)
    assert resp.status_code == 403
    resp = client.get("/api/admin/reports/revenue", headers=finance)
    assert resp.status_code == 200
    resp = client.post("/api/admin/services", json=_service_payload("rbac2"), headers=finance)
    assert resp.status_code == 403

    resp = client.get("/api/admin/services", headers=service_admin)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    other = make_catalog("other")
    resp = client.get(f"/api/admin/services/{other['service_id']}/products", headers=service_admin)
    assert resp.status_code == 403

    resp = client.patch(
        f"/api/admin/services/{other['service_id']}",
        json={"name": "Hacked"},
        headers=service_admin,
    )
    assert resp.status_code == 403

    resp = client.patch(
        f"/api/admin/services/{catalog['service_id']}",
        json={"name": "Renamed"},
        headers=service_admin,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


def test_service_token_issuance_and_revocation(client):
    catalog = make_catalog()
    resp = client.post(
        f"/api/admin/services/{catalog['service_id']}/tokens",
        json={"scopes": ["handoff", "entitlements:read", "entitlements:consume"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["token"].startswith("svc_")
    resp = client.delete(f"/api/admin/tokens/{data['id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["revoked_at"]

    resp = client.post(
        f"/api/admin/services/{catalog['service_id']}/tokens",
        json={"scopes": ["unknown_scope"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 400


def test_revenue_report_and_order_search(client, fake_bot):
    catalog = make_catalog(price=10)
    paid = buy_and_pay(client, catalog)
    resp = client.get("/api/admin/reports/revenue", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    report = resp.json()
    row = next(item for item in report["rows"] if item["service_code"] == "photo")
    assert row["orders"] == 1
    assert row["gross_stars"] == 10
    assert row["platform_fee_stars"] == 1
    assert row["service_share_stars"] == 9
    assert report["totals"]["gross_stars"] == 10

    resp = client.get(
        "/api/admin/reports/revenue", params={"format": "csv"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert "service_code" in resp.text

    resp = client.get(
        "/api/admin/orders",
        params={"telegram_user_id": 1001, "status": "paid"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == paid["order_id"]

    resp = client.get(
        "/api/admin/orders",
        params={"payment_id": "charge-1"},
        headers=ADMIN_HEADERS,
    )
    assert resp.json()["total"] == 1


def test_account_anonymization(client):
    from tests.helpers import make_account

    account_id = make_account()
    resp = client.post(
        f"/api/admin/users/{account_id}/anonymize", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["anonymized"] is True
    with session_scope() as db:
        from app.models import Account

        account = db.get(Account, account_id)
        assert account.email is None
        assert account.name is None
        assert account.is_blocked is True
        assert account.deleted_at is not None


def test_entitlement_adjust_and_audit(client):
    catalog = make_catalog(quantity=1)
    from tests.helpers import grant_access, make_account

    account_id = make_account()
    granted = grant_access(account_id, catalog)
    resp = client.post(
        f"/api/admin/entitlements/{granted['entitlement_id']}/adjust",
        json={"delta": 2, "reason": "adjust"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["remaining"] == 3

    resp = client.get("/api/admin/entitlements", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["remaining"] == 3

    resp = client.get("/api/admin/audit", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert any(item["action"] == "entitlement.adjust" for item in resp.json()["items"])

    resp = client.post(
        f"/api/admin/entitlements/{granted['entitlement_id']}/adjust",
        json={"delta": -100},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422
