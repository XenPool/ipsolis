"""Point-in-time access report — "who had access on date Y?".

The dashboard, order lists and cost report all answer "who has access
**today**". The audit-bearing question — *who had access to X on a past
date* — has no home yet: the audit-log route only filters a flat event
stream by timestamp, it never folds grant/revoke events into an access
*set* as of a date.

This report reconstructs that set by replaying ``order_change_log`` —
the immutable grant/revoke record already written by
``target_executor`` during provision/revoke (one row per
``(principal, target_type, identifier)`` action, with ``state`` and
``executed_at``). For a given ``as_of`` timestamp, the **latest
successful event** per key decides the state: a ``grant`` means access
was active, a ``revoke`` means it was not. Failed events are ignored
(a failed revoke leaves the prior grant standing; a failed grant never
took effect).

No new data model — this is a query over existing logs, mirroring the
cost report's ``?as_of=`` API/UI pattern (``admin_cost_report.py``).

Reads inherit the ``auditor`` role floor; there are no writes.
"""
from __future__ import annotations

import csv
import io
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.auth import require_admin_key, require_scopes
from app.utils.rbac import require_role

router = APIRouter(
    prefix="/admin/access-report",
    tags=["admin-access-report"],
    # Same read floor as the cost report: auditor+ with an orders:read
    # scope for token-driven reads. The report exposes only order-derived
    # access facts already visible to auditors elsewhere.
    dependencies=[
        Depends(require_admin_key),
        require_scopes("orders:read"),
        require_role("auditor"),
    ],
)


def _resolve_as_of(as_of: str | None) -> tuple[datetime, _date | None]:
    """Turn the ``as_of`` query param into an exclusive upper bound.

    Returns ``(cutoff, as_of_date)``. ``cutoff`` is a timezone-aware
    timestamp; access is reconstructed from all successful events with
    ``executed_at < cutoff``.

    * No ``as_of`` → live view: cutoff is "now" (UTC).
    * ``as_of=YYYY-MM-DD`` → include the *whole* of that day: cutoff is
      the following midnight UTC (``date + 1 day``). ``executed_at`` is
      stored in UTC, so an end-of-day boundary in UTC is the honest,
      reproducible choice for an audit reconstruction.
    """
    if not as_of:
        return datetime.now(timezone.utc), None
    try:
        d = _date.fromisoformat(as_of)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid as_of date: {as_of!r} (expected YYYY-MM-DD)",
        ) from exc
    cutoff = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
    return cutoff, d


async def _query_active_access(
    db: AsyncSession,
    cutoff: datetime,
    principal: str | None,
    asset_type_id: int | None,
) -> list[dict[str, Any]]:
    """Reconstruct the active access set as of ``cutoff``.

    ``DISTINCT ON (principal, target_type, identifier)`` ordered by
    ``executed_at DESC`` collapses each key to its latest **successful**
    event; the outer filter keeps only those whose latest event is a
    ``grant`` — i.e. access that stands as of the cutoff.
    """
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (cl.principal, cl.target_type, cl.identifier)
                   cl.principal,
                   cl.target_type,
                   cl.identifier,
                   cl.action,
                   cl.executed_at,
                   cl.order_id,
                   cl.resolved_object_id
            FROM order_change_log cl
            WHERE cl.state = 'success'
              AND cl.executed_at < :cutoff
            ORDER BY cl.principal, cl.target_type, cl.identifier,
                     cl.executed_at DESC, cl.id DESC
        )
        SELECT l.principal,
               l.target_type,
               l.identifier,
               l.executed_at        AS granted_at,
               l.order_id,
               l.resolved_object_id,
               o.user_email         AS order_user_email,
               o.user_name          AS order_user_name,
               at.id                AS asset_type_id,
               at.name              AS asset_type_name
        FROM latest l
        LEFT JOIN orders o       ON o.id = l.order_id
        LEFT JOIN asset_types at ON at.id = o.asset_type_id
        WHERE l.action = 'grant'
          -- Explicit casts: asyncpg cannot infer the type of a bound NULL
          -- parameter used only in an IS NULL test (AmbiguousParameterError).
          AND (CAST(:principal AS text) IS NULL OR l.principal ILIKE :principal_like)
          AND (CAST(:asset_type_id AS integer) IS NULL OR at.id = :asset_type_id)
        ORDER BY l.principal, at.name NULLS LAST, l.target_type, l.identifier
    """
    params = {
        "cutoff": cutoff,
        "principal": principal,
        "principal_like": f"%{principal}%" if principal else None,
        "asset_type_id": asset_type_id,
    }
    rows = await db.execute(text(sql), params)
    out: list[dict[str, Any]] = []
    for r in rows.mappings().all():
        out.append({
            "principal":         r["principal"],
            "target_type":       r["target_type"],
            "identifier":        r["identifier"],
            "asset_type_id":     r["asset_type_id"],
            "asset_type_name":   r["asset_type_name"] or "(order deleted)",
            "order_id":          r["order_id"],
            "order_user_email":  r["order_user_email"] or "",
            "resolved_object_id": r["resolved_object_id"] or "",
            "granted_at":        r["granted_at"].isoformat() if r["granted_at"] else "",
        })
    return out


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the flat access rows into report summary structures."""
    principals: set[str] = set()
    by_asset: dict[tuple[Any, str], dict[str, Any]] = {}
    by_target: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = (r["principal"] or "").lower()
        principals.add(p)

        ak = (r["asset_type_id"], r["asset_type_name"])
        a = by_asset.setdefault(ak, {
            "asset_type_id": r["asset_type_id"],
            "asset_type_name": r["asset_type_name"],
            "grants": 0,
            "_principals": set(),
        })
        a["grants"] += 1
        a["_principals"].add(p)

        tk = r["target_type"] or "(unknown)"
        t = by_target.setdefault(tk, {
            "target_type": tk,
            "grants": 0,
            "_principals": set(),
        })
        t["grants"] += 1
        t["_principals"].add(p)

    by_asset_out = sorted(
        (
            {
                "asset_type_id": v["asset_type_id"],
                "asset_type_name": v["asset_type_name"],
                "grants": v["grants"],
                "principals": len(v["_principals"]),
            }
            for v in by_asset.values()
        ),
        key=lambda x: (x["asset_type_name"] or ""),
    )
    by_target_out = sorted(
        (
            {
                "target_type": v["target_type"],
                "grants": v["grants"],
                "principals": len(v["_principals"]),
            }
            for v in by_target.values()
        ),
        key=lambda x: x["target_type"],
    )
    return {
        "total_grants": len(rows),
        "distinct_principals": len(principals),
        "by_asset_type": by_asset_out,
        "by_target_type": by_target_out,
    }


@router.get("", response_model=None)
async def access_report(
    fmt: str = Query(default="json", regex="^(json|csv)$"),
    as_of: str | None = Query(default=None, regex=r"^\d{4}-\d{2}-\d{2}$"),
    principal: str | None = Query(default=None, max_length=255),
    asset_type_id: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Point-in-time access report.

    Query params:

    * ``as_of`` (``YYYY-MM-DD``) — reconstruct the access set as it stood
      at the **end of that day (UTC)**. Omitted → the live "as of now"
      set. Unlike the cost report, this reads no snapshot table: the
      answer is replayed directly from the immutable ``order_change_log``,
      so any past date is answerable without a pre-computed snapshot.
    * ``principal`` — case-insensitive substring filter on the granted
      user (email / sAMAccountName).
    * ``asset_type_id`` — restrict to a single asset definition.
    * ``fmt`` — ``json`` (default, powers the UI) or ``csv`` (per-grant
      detail for offline audit evidence).
    """
    cutoff, as_of_date = _resolve_as_of(as_of)
    rows = await _query_active_access(db, cutoff, principal, asset_type_id)

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Principal",
            "Target type",
            "Identifier",
            "Asset definition",
            "Order ID",
            "Order user email",
            "Resolved object id",
            "Granted at",
        ])
        for r in rows:
            writer.writerow([
                r["principal"],
                r["target_type"],
                r["identifier"],
                r["asset_type_name"],
                r["order_id"],
                r["order_user_email"],
                r["resolved_object_id"],
                r["granted_at"],
            ])
        as_of_tag = as_of_date.isoformat() if as_of_date else "now"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="ipsolis-access-report-{as_of_tag}.csv"'
            },
        )

    return {
        "rows": rows,
        "summary": _summarise(rows),
        "meta": {
            "as_of": as_of_date.isoformat() if as_of_date else None,
            "live": as_of_date is None,
            "cutoff": cutoff.isoformat(),
        },
    }
