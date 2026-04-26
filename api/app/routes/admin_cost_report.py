"""Cost / chargeback report.

Two complementary views from the same active-orders dataset:

* **Provider side** (asset definition's ``cost_center``) — bills the team
  that owns / operates the asset. Drives the "what does each platform
  team produce?" question.
* **Consumer side** (the requester's AD ``cost_center`` / ``department``,
  snapshot at order-creation time on the order row) — bills the team
  using the asset. Drives the "what does each business unit consume?"
  question.

CSV export defaults to per-order detail with full requester info so
finance / HR can pivot the data however they need. JSON powers the
dashboard with both summary aggregations.

"Active" matches the same set used by capacity / quota enforcement —
``pending``, ``pending_approval``, ``scheduled``, ``processing``,
``provisioning``, ``provisioned``, ``delivered``.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.auth import require_admin_key, require_scopes

router = APIRouter(
    prefix="/admin/cost-report",
    tags=["admin-cost-report"],
    dependencies=[Depends(require_admin_key), require_scopes("orders:read")],
)

_ACTIVE_ORDER_STATUSES = (
    "pending", "pending_approval", "scheduled",
    "processing", "provisioning", "provisioned", "delivered",
)


async def _query_active_orders(db: AsyncSession) -> list[dict[str, Any]]:
    """Return one dict per active order joined with asset-type cost data
    and the requester AD snapshot. Excludes orders against asset types
    that have no ``monthly_cost`` set — those are untracked.
    """
    sql = """
        SELECT
            o.id            AS order_id,
            o.user_email,
            o.user_name,
            o.requester_sam_account,
            o.requester_department,
            o.requester_cost_center,
            o.requester_company,
            o.requester_employee_id,
            o.requester_title,
            o.status::text  AS status,
            o.created_at,
            o.requested_from,
            o.requested_until,
            at.id           AS asset_type_id,
            at.name         AS asset_type_name,
            at.cost_center  AS provider_cost_center,
            at.currency,
            at.monthly_cost
        FROM orders o
        JOIN asset_types at ON at.id = o.asset_type_id
        WHERE at.monthly_cost IS NOT NULL
          AND o.status::text = ANY(:active_statuses)
        ORDER BY at.cost_center NULLS LAST, at.name, o.id
    """
    rows = await db.execute(text(sql), {"active_statuses": list(_ACTIVE_ORDER_STATUSES)})
    out: list[dict[str, Any]] = []
    for r in rows.mappings().all():
        unit = float(r["monthly_cost"]) if r["monthly_cost"] is not None else 0.0
        out.append({
            "order_id":              r["order_id"],
            "user_email":            r["user_email"],
            "user_name":             r["user_name"],
            "requester_sam_account": r["requester_sam_account"] or "",
            "requester_department":  r["requester_department"]  or "",
            "requester_cost_center": r["requester_cost_center"] or "",
            "requester_company":     r["requester_company"]     or "",
            "requester_employee_id": r["requester_employee_id"] or "",
            "requester_title":       r["requester_title"]       or "",
            "status":                r["status"],
            "created_at":            r["created_at"].isoformat() if r["created_at"] else "",
            "requested_from":        r["requested_from"].isoformat() if r["requested_from"] else "",
            "requested_until":       r["requested_until"].isoformat() if r["requested_until"] else "",
            "asset_type_id":         r["asset_type_id"],
            "asset_type_name":       r["asset_type_name"],
            "provider_cost_center":  r["provider_cost_center"] or "(unassigned)",
            "currency":              r["currency"] or "",
            "unit_monthly_cost":     unit,
            "monthly_total":         unit,
        })
    return out


def _aggregate_by(rows: list[dict[str, Any]], key_field: str) -> list[dict[str, Any]]:
    """Group ``rows`` by ``(rows[i][key_field], currency)`` and sum totals."""
    buckets: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"projected_monthly_total": 0.0, "active_orders": 0, "asset_types": set()}
    )
    for r in rows:
        bucket_key = r.get(key_field) or "(unassigned)"
        key = (bucket_key, r["currency"])
        b = buckets[key]
        b["projected_monthly_total"] += r["monthly_total"]
        b["active_orders"] += 1
        b["asset_types"].add(r["asset_type_id"])
    return [
        {
            "key": k,
            "currency": cur,
            "projected_monthly_total": round(v["projected_monthly_total"], 2),
            "active_orders": v["active_orders"],
            "asset_types": len(v["asset_types"]),
        }
        for (k, cur), v in sorted(buckets.items())
    ]


@router.get("", response_model=None)
async def cost_report(
    fmt: str = Query(default="json", regex="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    rows = await _query_active_orders(db)

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Order ID",
            "Status",
            "Created at",
            "Requested from",
            "Requested until",
            "User email",
            "User name",
            "sAMAccountName",
            "Employee ID",
            "Title",
            "Department",
            "Requester cost center",
            "Company",
            "Asset type",
            "Provider cost center",
            "Currency",
            "Unit monthly cost",
            "Monthly total",
        ])
        for r in rows:
            writer.writerow([
                r["order_id"],
                r["status"],
                r["created_at"],
                r["requested_from"],
                r["requested_until"],
                r["user_email"],
                r["user_name"],
                r["requester_sam_account"],
                r["requester_employee_id"],
                r["requester_title"],
                r["requester_department"],
                r["requester_cost_center"],
                r["requester_company"],
                r["asset_type_name"],
                r["provider_cost_center"],
                r["currency"],
                f"{r['unit_monthly_cost']:.2f}",
                f"{r['monthly_total']:.2f}",
            ])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="ipsolis-cost-report.csv"'},
        )

    # JSON view: provider summary + consumer summary + per-(asset-type, provider) detail.
    # Keep ``rows`` (provider-side aggregate) for back-compat with the dashboard
    # rendered before this slice landed.
    by_provider_at: dict[tuple[str, int], dict[str, Any]] = {}
    for r in rows:
        key = (r["provider_cost_center"], r["asset_type_id"])
        agg = by_provider_at.setdefault(key, {
            "cost_center": r["provider_cost_center"],
            "asset_type_id": r["asset_type_id"],
            "asset_type_name": r["asset_type_name"],
            "currency": r["currency"],
            "unit_monthly_cost": r["unit_monthly_cost"],
            "active_orders": 0,
            "unique_users": set(),
            "projected_monthly_total": 0.0,
        })
        agg["active_orders"] += 1
        agg["projected_monthly_total"] += r["monthly_total"]
        if r["user_email"]:
            agg["unique_users"].add(r["user_email"].lower())

    legacy_rows = [
        {
            "cost_center": v["cost_center"],
            "asset_type_id": v["asset_type_id"],
            "asset_type_name": v["asset_type_name"],
            "currency": v["currency"],
            "unit_monthly_cost": v["unit_monthly_cost"],
            "active_orders": v["active_orders"],
            "unique_users": len(v["unique_users"]),
            "projected_monthly_total": round(v["projected_monthly_total"], 2),
        }
        for v in by_provider_at.values()
    ]
    # Asset definitions priced but with zero active orders — surface them
    # as 0-row entries so admins see the type is configured.
    sql_unused = """
        SELECT id, name, COALESCE(NULLIF(cost_center, ''), '(unassigned)') AS cost_center,
               currency, monthly_cost
        FROM asset_types
        WHERE monthly_cost IS NOT NULL
          AND id NOT IN (
            SELECT asset_type_id FROM orders
            WHERE status::text = ANY(:active_statuses)
          )
    """
    unused = await db.execute(text(sql_unused), {"active_statuses": list(_ACTIVE_ORDER_STATUSES)})
    for u in unused.mappings().all():
        legacy_rows.append({
            "cost_center": u["cost_center"],
            "asset_type_id": u["id"],
            "asset_type_name": u["name"],
            "currency": u["currency"] or "",
            "unit_monthly_cost": float(u["monthly_cost"]),
            "active_orders": 0,
            "unique_users": 0,
            "projected_monthly_total": 0.0,
        })
    legacy_rows.sort(key=lambda r: (r["cost_center"], r["asset_type_name"]))

    return {
        "rows": legacy_rows,
        "totals": _aggregate_by(rows, "provider_cost_center"),
        "by_consumer_cost_center": _aggregate_by(rows, "requester_cost_center"),
        "by_consumer_department":  _aggregate_by(rows, "requester_department"),
    }
