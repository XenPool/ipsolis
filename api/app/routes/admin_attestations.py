"""Admin read API for attestation artifacts (handover + revocation).

Compliance-evidence view: outstanding / completed handover acknowledgments and
issued revocation certificates. Read-only (auditor floor) — artifacts are
emitted by the worker on order lifecycle transitions, never hand-created here.
The signed viewer link per row lets an admin re-open the exact page the
recipient sees.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.attestation_token import make_attestation_token
from app.utils.auth import require_admin_key
from app.utils.rbac import require_role

router = APIRouter(
    prefix="/admin/attestations",
    tags=["admin-attestations"],
    dependencies=[Depends(require_admin_key), require_role("auditor")],
)


@router.get("")
async def list_attestations(
    kind: str | None = Query(default=None, description="handover | revocation"),
    status: str | None = Query(default=None, description="pending | acknowledged | emitted"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    where = ["1=1"]
    params: dict[str, Any] = {}
    if kind in ("handover", "revocation"):
        where.append("a.kind = :kind")
        params["kind"] = kind
    if status in ("pending", "acknowledged", "emitted"):
        where.append("a.status = :status")
        params["status"] = status

    rows = (await db.execute(text(
        f"""
        SELECT a.id, a.kind, a.order_id, a.asset_type_id, a.recipient_email,
               a.recipient_name, a.status, a.acknowledged_at, a.acknowledged_by,
               a.created_at, at.name AS asset_type_name
        FROM attestation_artifacts a
        LEFT JOIN asset_types at ON at.id = a.asset_type_id
        WHERE {' AND '.join(where)}
        ORDER BY a.created_at DESC
        LIMIT 500
        """
    ), params)).mappings().all()

    items = [
        {
            "id": r["id"],
            "kind": r["kind"],
            "order_id": r["order_id"],
            "asset_type_name": r["asset_type_name"],
            "recipient_email": r["recipient_email"],
            "recipient_name": r["recipient_name"],
            "status": r["status"],
            "acknowledged_at": r["acknowledged_at"].isoformat() if r["acknowledged_at"] else None,
            "acknowledged_by": r["acknowledged_by"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "view_url": f"/attestation/{make_attestation_token(r['id'])}",
        }
        for r in rows
    ]
    # Small headline counts for the page tiles.
    counts = {k: v for k, v in (await db.execute(text(
        "SELECT status, COUNT(*) FROM attestation_artifacts GROUP BY status"
    ))).all()}
    return {
        "items": items,
        "counts": {
            "pending": int(counts.get("pending", 0)),
            "acknowledged": int(counts.get("acknowledged", 0)),
            "emitted": int(counts.get("emitted", 0)),
        },
    }
