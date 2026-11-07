"""Revenue and order reports."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, Service


def _filtered(
    query,
    date_column,
    date_from: datetime | None,
    date_to: datetime | None,
    service_id: str | None,
):
    if date_from is not None:
        query = query.where(date_column >= date_from)
    if date_to is not None:
        query = query.where(date_column <= date_to)
    if service_id:
        query = query.where(Order.service_id == service_id)
    return query


def revenue(
    db: Session,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    service_id: str | None = None,
) -> dict:
    paid_query = _filtered(
        select(
            Order.service_id,
            func.count(Order.id),
            func.coalesce(func.sum(Order.price_stars), 0),
            func.coalesce(func.sum(Order.platform_fee_stars), 0),
            func.coalesce(func.sum(Order.service_share_stars), 0),
        ).where(Order.status == "paid"),
        Order.paid_at,
        date_from,
        date_to,
        service_id,
    ).group_by(Order.service_id)
    paid = {
        row[0]: {
            "orders": int(row[1]),
            "gross_stars": int(row[2]),
            "platform_fee_stars": int(row[3]),
            "service_share_stars": int(row[4]),
        }
        for row in db.execute(paid_query)
    }
    refund_query = _filtered(
        select(Order.service_id, func.count(Order.id), func.coalesce(func.sum(Order.price_stars), 0))
        .where(Order.status == "refunded"),
        Order.refunded_at,
        date_from,
        date_to,
        service_id,
    ).group_by(Order.service_id)
    refunds = {
        row[0]: {"orders": int(row[1]), "refunded_stars": int(row[2])}
        for row in db.execute(refund_query)
    }
    services = list(db.execute(select(Service).order_by(Service.sort_order, Service.name)).scalars())
    rows = []
    totals = {
        "orders": 0,
        "gross_stars": 0,
        "platform_fee_stars": 0,
        "service_share_stars": 0,
        "refunded_stars": 0,
        "refund_orders": 0,
    }
    for service in services:
        if service_id and service.id != service_id:
            continue
        paid_row = paid.get(
            service.id,
            {"orders": 0, "gross_stars": 0, "platform_fee_stars": 0, "service_share_stars": 0},
        )
        refund_row = refunds.get(service.id, {"orders": 0, "refunded_stars": 0})
        row = {
            "service_id": service.id,
            "service_code": service.code,
            "service_name": service.name,
            "orders": paid_row["orders"],
            "gross_stars": paid_row["gross_stars"],
            "platform_fee_stars": paid_row["platform_fee_stars"],
            "service_share_stars": paid_row["service_share_stars"],
            "refund_orders": refund_row["orders"],
            "refunded_stars": refund_row["refunded_stars"],
        }
        rows.append(row)
        for key in ("orders", "gross_stars", "platform_fee_stars", "service_share_stars"):
            totals[key] += row[key]
        totals["refund_orders"] += row["refund_orders"]
        totals["refunded_stars"] += row["refunded_stars"]
    return {"rows": rows, "totals": totals}


REVENUE_CSV_FIELDS = (
    "service_code",
    "service_name",
    "orders",
    "gross_stars",
    "platform_fee_stars",
    "service_share_stars",
    "refund_orders",
    "refunded_stars",
)


def revenue_csv(report: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(REVENUE_CSV_FIELDS)
    for row in report["rows"]:
        writer.writerow([row[field] for field in REVENUE_CSV_FIELDS])
    return buffer.getvalue()
