"""Cost / chargeback report + threshold alerting.

Two complementary report views from the same active-orders dataset:

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

Threshold alerts (``cost_thresholds`` table) live on the same router
since they're operationally inseparable from the report — admins set
limits next to the figures they're reading. Reads inherit the router
floor (``auditor``); writes carry an explicit ``admin`` guard.
"""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Any

import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.config import AppConfig
from app.models.cost_report_snapshot import CostReportSnapshot
from app.models.cost_threshold import CostThreshold
from app.utils.auth import require_admin_key, require_scopes
from app.utils.rbac import require_role

router = APIRouter(
    prefix="/admin/cost-report",
    tags=["admin-cost-report"],
    # Read floor: auditor+. Per-route ``require_role("admin")`` raises
    # the bar on threshold writes; ``require_scopes("config:write")``
    # gates token-driven writes.
    dependencies=[
        Depends(require_admin_key),
        require_scopes("orders:read"),
        require_role("auditor"),
    ],
)

# Per-route write guard — auditor+ can read the report but only admin+
# may add/edit/delete cost thresholds.
_THRESHOLD_WRITE_GATE = require_role("admin")

_ACTIVE_ORDER_STATUSES = (
    "pending", "pending_approval", "scheduled",
    "processing", "provisioning", "provisioned", "delivered",
)


# ── FX conversion (config-driven static rates) ──────────────────────────────

async def _load_fx_config(db: AsyncSession) -> tuple[str, dict[str, float]]:
    """Read the canonical reporting currency + per-currency rate map.

    Returns ``(canonical, rates)`` where ``rates[currency]`` is the
    conversion factor INTO ``canonical`` (e.g. ``rates["USD"] = 0.92``
    when canonical is EUR means 1 USD → 0.92 EUR). Missing currencies in
    the map are treated as "no rate available" and skipped during
    conversion (caller decides whether to fall back to per-currency view
    or just exclude the row).
    """
    result = await db.execute(
        select(AppConfig).where(AppConfig.key.in_(("cost.fx.canonical", "cost.fx.rates")))
    )
    cfg = {c.key: (c.value or "") for c in result.scalars().all()}
    canonical = (cfg.get("cost.fx.canonical") or "EUR").strip().upper() or "EUR"
    rates_raw = (cfg.get("cost.fx.rates") or "{}").strip() or "{}"
    try:
        parsed = json.loads(rates_raw)
        if not isinstance(parsed, dict):
            parsed = {}
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    rates: dict[str, float] = {}
    for k, v in parsed.items():
        try:
            rates[str(k).strip().upper()] = float(v)
        except (TypeError, ValueError):
            continue
    # The canonical currency is itself rate=1.00 even if not declared.
    rates.setdefault(canonical, 1.0)
    return canonical, rates


def _convert_row_amounts(
    row: dict[str, Any], canonical: str, rates: dict[str, float]
) -> dict[str, Any] | None:
    """Apply FX to a single aggregated dict.

    Returns a copy of ``row`` with ``projected_monthly_total`` / ``unit``
    fields converted to ``canonical`` and ``currency`` overwritten to
    ``canonical``. Returns ``None`` when the source currency isn't in the
    rates map (caller decides whether to drop or surface as "untracked").
    """
    src = (row.get("currency") or "").upper()
    if not src:
        return None
    rate = rates.get(src)
    if rate is None or rate <= 0:
        return None
    out = dict(row)
    out["currency"] = canonical
    if "projected_monthly_total" in out and out["projected_monthly_total"] is not None:
        out["projected_monthly_total"] = round(float(out["projected_monthly_total"]) * rate, 2)
    if "unit_monthly_cost" in out and out["unit_monthly_cost"] is not None:
        out["unit_monthly_cost"] = round(float(out["unit_monthly_cost"]) * rate, 2)
    return out


def _aggregate_converted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-aggregate already-converted rows by ``key`` (currency is uniform).

    After FX, all rows share ``currency = canonical`` so the report's
    summary cards collapse mixed-currency cost centers into a single
    figure per cost-center key.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.get("key") or r.get("cost_center") or "(unassigned)"
        b = buckets.setdefault(key, {
            "key": key,
            "currency": r["currency"],
            "projected_monthly_total": 0.0,
            "active_orders": 0,
            "asset_types": 0,
        })
        b["projected_monthly_total"] += float(r.get("projected_monthly_total", 0) or 0)
        b["active_orders"] += int(r.get("active_orders", 0) or 0)
        b["asset_types"] += int(r.get("asset_types", 0) or 0)
    out = []
    for key, b in buckets.items():
        b["projected_monthly_total"] = round(b["projected_monthly_total"], 2)
        out.append(b)
    return sorted(out, key=lambda r: r["key"])


# ── Historical snapshots ─────────────────────────────────────────────────────

async def _load_snapshot_views(
    db: AsyncSession, as_of: _date
) -> dict[str, list[dict[str, Any]]] | None:
    """Return summary aggregations for ``as_of`` from the snapshot table.

    Returns ``None`` when no snapshot rows exist for that date — caller
    falls back to the live view in that case (typical for "today" before
    the daily Beat task has run).
    """
    rows = (await db.execute(
        select(CostReportSnapshot).where(CostReportSnapshot.snapshot_date == as_of)
    )).scalars().all()
    if not rows:
        return None
    by_view: dict[str, list[dict[str, Any]]] = {
        "totals": [],
        "by_consumer_cost_center": [],
        "by_consumer_department": [],
    }
    view_to_key = {
        "provider": "totals",
        "consumer_cc": "by_consumer_cost_center",
        "consumer_dept": "by_consumer_department",
    }
    for r in rows:
        out_key = view_to_key.get(r.view)
        if out_key is None:
            continue
        by_view[out_key].append({
            "key": r.dimension_key,
            "currency": r.currency,
            "projected_monthly_total": float(r.projected_monthly_total),
            "active_orders": r.active_orders,
            "asset_types": r.asset_types,
        })
    for v in by_view.values():
        v.sort(key=lambda x: (x["key"], x["currency"]))
    return by_view


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
    reporting_currency: str | None = Query(default=None, regex="^[A-Za-z]{3}$"),
    as_of: str | None = Query(default=None, regex=r"^\d{4}-\d{2}-\d{2}$"),
    db: AsyncSession = Depends(get_db),
):
    """Cost report.

    Query params:

    * ``reporting_currency`` (e.g. ``EUR``) — convert mixed-currency
      totals into a single reporting currency using the static rate
      table in ``app_config.cost.fx.rates``. Rows in unknown currencies
      are excluded (logged via ``meta.fx_excluded_currencies``).
    * ``as_of`` (``YYYY-MM-DD``) — render the snapshot stored in
      ``cost_report_snapshots`` for that date instead of the live
      "currently active" computation. Falls back to live data when no
      snapshot exists for the date (typical for "today" before the
      daily Beat task has run). Has no effect on the per-order CSV
      export — that path always reads live data.
    """
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

    # ── as_of: try the snapshot store first, fall back to live ──────────────
    snapshot_views: dict[str, list[dict[str, Any]]] | None = None
    snapshot_used = False
    snapshot_date_used: _date | None = None
    if as_of:
        try:
            target = _date.fromisoformat(as_of)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid as_of date: {as_of!r} (expected YYYY-MM-DD)",
            ) from exc
        snapshot_views = await _load_snapshot_views(db, target)
        if snapshot_views is not None:
            snapshot_used = True
            snapshot_date_used = target

    if snapshot_used and snapshot_views is not None:
        totals = snapshot_views["totals"]
        by_consumer_cc = snapshot_views["by_consumer_cost_center"]
        by_consumer_dept = snapshot_views["by_consumer_department"]
        # The detail-rows table is per-asset-type and only stored on live
        # data; show empty when reading a snapshot so the UI doesn't pretend
        # to have point-in-time per-row context.
        legacy_rows_out: list[dict[str, Any]] = []
    else:
        totals = _aggregate_by(rows, "provider_cost_center")
        by_consumer_cc = _aggregate_by(rows, "requester_cost_center")
        by_consumer_dept = _aggregate_by(rows, "requester_department")
        legacy_rows_out = legacy_rows

    # ── Optional FX conversion (applies to summary cards only) ──────────────
    meta: dict[str, Any] = {
        "as_of": snapshot_date_used.isoformat() if snapshot_date_used else None,
        "snapshot": snapshot_used,
        "fx_applied": False,
        "fx_canonical": None,
        "fx_excluded_currencies": [],
    }
    if reporting_currency:
        target_cur = reporting_currency.upper()
        canonical, rates = await _load_fx_config(db)
        # If admin requested a non-canonical reporting currency, fold it in:
        # we treat the requested currency as the new "canonical" for this
        # response and assume the configured rates already encode INTO the
        # canonical. To convert into a different reporting currency we'd
        # need cross-rates; if the requested currency isn't in the rate
        # map we keep it but note in meta.
        excluded: set[str] = set()

        def _convert_list(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
            converted: list[dict[str, Any]] = []
            for r in values:
                src = (r.get("currency") or "").upper()
                if not src:
                    continue
                if src == target_cur:
                    converted.append({**r, "currency": target_cur})
                    continue
                # rate(src) → canonical; rate(target_cur) → canonical too.
                # Cross-rate: src→target = rate(src) / rate(target_cur).
                rate_src = rates.get(src)
                rate_tgt = rates.get(target_cur)
                if rate_src is None or rate_tgt is None or rate_tgt <= 0:
                    excluded.add(src)
                    continue
                factor = rate_src / rate_tgt
                copy = dict(r)
                copy["currency"] = target_cur
                if "projected_monthly_total" in copy and copy["projected_monthly_total"] is not None:
                    copy["projected_monthly_total"] = round(
                        float(copy["projected_monthly_total"]) * factor, 2
                    )
                if "unit_monthly_cost" in copy and copy["unit_monthly_cost"] is not None:
                    copy["unit_monthly_cost"] = round(
                        float(copy["unit_monthly_cost"]) * factor, 2
                    )
                converted.append(copy)
            return converted

        # Apply + re-aggregate the summary cards so mixed-currency cost
        # centers collapse into a single figure.
        totals = _aggregate_converted(_convert_list(totals))
        by_consumer_cc = _aggregate_converted(_convert_list(by_consumer_cc))
        by_consumer_dept = _aggregate_converted(_convert_list(by_consumer_dept))
        # Per-row detail table: convert in place but don't re-aggregate
        # (rows are per-asset-type, FX doesn't change cardinality).
        legacy_rows_out = _convert_list(legacy_rows_out)

        meta["fx_applied"] = True
        meta["fx_canonical"] = target_cur
        meta["fx_excluded_currencies"] = sorted(excluded)

    return {
        "rows": legacy_rows_out,
        "totals": totals,
        "by_consumer_cost_center": by_consumer_cc,
        "by_consumer_department":  by_consumer_dept,
        "meta": meta,
    }


# ── FX config (read-only — admins set rates via Settings → Finance) ───────────

@router.get("/fx-config")
async def get_fx_config(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Return the canonical reporting currency + the currencies for which
    rates are configured. The UI populates the "Show in" selector from
    this list so an admin can't pick a currency we can't convert to.
    """
    canonical, rates = await _load_fx_config(db)
    return {
        "canonical": canonical,
        "available_currencies": sorted(rates.keys()),
        "rates": rates,
    }


# ── Cost thresholds — CRUD ─────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalise_recipients(raw: str) -> str:
    """Split a comma-separated recipient string, validate, rejoin."""
    if not raw:
        raise ValueError("at least one recipient required")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("at least one recipient required")
    for p in parts:
        if not _EMAIL_RE.match(p):
            raise ValueError(f"invalid email: {p!r}")
    return ", ".join(parts)


class _RecipientList(BaseModel):
    """Mixin validator: comma-separated string → trimmed, validated rejoin."""
    @field_validator("recipients", check_fields=False)
    @classmethod
    def _parse_recipients(cls, v: str) -> str:
        return _normalise_recipients(v)


class CostThresholdCreate(_RecipientList):
    cost_center: str = Field(min_length=1, max_length=100)
    currency: str = Field(min_length=3, max_length=3)
    monthly_limit: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    recipients: str

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


class CostThresholdUpdate(_RecipientList):
    monthly_limit: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    recipients: str


def _threshold_dict(t: CostThreshold) -> dict[str, Any]:
    return {
        "cost_center": t.cost_center,
        "currency": t.currency,
        "monthly_limit": float(t.monthly_limit),
        "recipients": t.recipients,
        "last_alerted_at": t.last_alerted_at.isoformat() if t.last_alerted_at else None,
        "last_alerted_amount": (
            float(t.last_alerted_amount) if t.last_alerted_amount is not None else None
        ),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("/thresholds")
async def list_cost_thresholds(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """Return all configured cost thresholds, ordered by cost center then currency."""
    result = await db.execute(
        select(CostThreshold).order_by(CostThreshold.cost_center, CostThreshold.currency)
    )
    return [_threshold_dict(t) for t in result.scalars().all()]


@router.post(
    "/thresholds",
    status_code=status.HTTP_201_CREATED,
    dependencies=[_THRESHOLD_WRITE_GATE, require_scopes("config:write")],
)
async def create_cost_threshold(
    payload: CostThresholdCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    existing = await db.get(CostThreshold, (payload.cost_center, payload.currency))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Threshold {payload.cost_center}/{payload.currency} already exists",
        )
    t = CostThreshold(
        cost_center=payload.cost_center,
        currency=payload.currency,
        monthly_limit=payload.monthly_limit,
        recipients=payload.recipients,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _threshold_dict(t)


@router.put(
    "/thresholds/{cost_center}/{currency}",
    dependencies=[_THRESHOLD_WRITE_GATE, require_scopes("config:write")],
)
async def update_cost_threshold(
    cost_center: str,
    currency: str,
    payload: CostThresholdUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    t = await db.get(CostThreshold, (cost_center, currency.upper()))
    if not t:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Threshold {cost_center}/{currency} not found",
        )
    t.monthly_limit = payload.monthly_limit
    t.recipients = payload.recipients
    # Editing the limit/recipients clears the last-alerted clock so the
    # next breach re-alerts immediately rather than waiting out the
    # quiet window with stale settings.
    t.last_alerted_at = None
    t.last_alerted_amount = None
    await db.commit()
    await db.refresh(t)
    return _threshold_dict(t)


@router.delete(
    "/thresholds/{cost_center}/{currency}",
    status_code=status.HTTP_204_NO_CONTENT,
    # ``response_model=None`` is mandatory here: this module imports
    # ``from __future__ import annotations``, which keeps function return
    # annotations as strings. FastAPI then can't resolve ``-> None`` to
    # the literal None and trips its 204-no-body assertion. Explicit
    # ``response_model=None`` skips the inference and the assertion.
    response_model=None,
    dependencies=[_THRESHOLD_WRITE_GATE, require_scopes("config:write")],
)
async def delete_cost_threshold(
    cost_center: str,
    currency: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    t = await db.get(CostThreshold, (cost_center, currency.upper()))
    if not t:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Threshold {cost_center}/{currency} not found",
        )
    await db.delete(t)
    await db.commit()

