"""Admin API: configuration migration export / import (JSON).

Standing up a fresh instance means re-entering asset-type config by hand.
This endpoint pair moves the config-bearing tables between instances as a
single JSON document:

* ``GET  /admin/migration/export`` — download the current **asset types**
  and **asset-pool instances** as a portable JSON document.
* ``POST /admin/migration/import`` — load such a document into a (typically
  fresh) instance.

Design mirrors the seed-export mechanism (``admin_seed_export.py``): JSON,
**name-referenced** (pool instances reference their asset type by *name*,
so ids need not line up across instances), and **insert-only** — an import
never overwrites an existing row (same contract as migration 0046). Re-running
an import is therefore safe and idempotent for already-present names.

Not a backup: runtime state (orders, approvals, audit log, pool status,
current_order) is intentionally out of scope — for full DR use the backup /
restore path (see docs/DR-RUNBOOK.md). Bundles are a planned follow-up, gated
on the Onboarding-bundles entity landing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import AssetCategory, AssetPool, AssetStatus, AssetType
from app.utils.asset_type_constraints import validate_asset_type
from app.utils.audit import aaudit
from app.utils.auth import require_admin_key, require_scopes
from app.utils.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/migration",
    tags=["admin-migration"],
    # Migration is an admin operation. Reads carry ``config:read`` and
    # writes ``config:write`` for token-driven access, both gated at
    # ``admin`` (below the superadmin bar the seed-to-disk export uses,
    # since this touches no shipped image content — only runtime rows).
    dependencies=[Depends(require_admin_key), require_role("admin")],
)

MIGRATION_FORMAT_VERSION = 1

# Asset-type columns carried by the migration document. Excludes id /
# timestamps / relationships; the name is the natural key.
_ASSET_TYPE_FIELDS = (
    "name", "description", "help_text", "is_active", "show_on_dashboard",
    "category", "config", "assignment_model", "pool_capacity",
    "automation_mode", "targets", "automation_strategy", "composite_steps",
    "deprovision_policy", "personal_provisioning_strategy", "naming_pattern",
    "max_per_user", "monthly_cost", "currency", "cost_center",
    "lifecycle_ttl_days", "lifecycle_renewable", "lifecycle_reminder_days",
    "allow_rdp_users", "allow_admin_users", "rds_gateway_url",
    "requires_manager_approval", "requires_owner_approval", "approval_owners",
    "approval_rules", "min_approvals_required", "requires_approval_on_modify",
    "eligible_requestors_dn", "logo", "drift_monitor",
)


# ── Export ────────────────────────────────────────────────────────────────────

def _serialise_asset_type(at: AssetType) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in _ASSET_TYPE_FIELDS:
        val = getattr(at, f)
        if f == "category":
            val = val.value if hasattr(val, "value") else val
        elif f == "monthly_cost":
            val = float(val) if val is not None else None
        out[f] = val
    return out


@router.get("/export", dependencies=[require_scopes("config:read")])
async def export_config(
    download: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export asset types + asset-pool instances as one JSON document.

    Pool instances reference their asset type by ``asset_type`` (name) so
    the document is portable across instances with different ids. Runtime
    state (status, current order) is not exported — imported instances
    start ``Free``.
    """
    types = (await db.execute(select(AssetType).order_by(AssetType.name))).scalars().all()
    type_name_by_id = {at.id: at.name for at in types}

    pool = (await db.execute(select(AssetPool).order_by(AssetPool.name))).scalars().all()

    doc: dict[str, Any] = {
        "ipsolis_migration": {
            "format_version": MIGRATION_FORMAT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "kinds": ["asset_types", "asset_pool"],
        },
        "asset_types": [_serialise_asset_type(at) for at in types],
        "asset_pool": [
            {
                "name": a.name,
                "asset_type": type_name_by_id.get(a.asset_type_id),
                "metadata": a.asset_metadata,
            }
            for a in pool
        ],
    }

    import json as _json
    body = _json.dumps(doc, indent=2, ensure_ascii=False, default=str)
    headers = {}
    if download:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        headers["Content-Disposition"] = (
            f'attachment; filename="ipsolis-config-{ts}.json"'
        )
    return Response(content=body, media_type="application/json", headers=headers)


# ── Import ────────────────────────────────────────────────────────────────────

def _to_decimal(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _build_asset_type(row: dict[str, Any]) -> AssetType:
    """Construct an AssetType from an export row (validated separately)."""
    return AssetType(
        name=row["name"],
        description=row.get("description"),
        help_text=row.get("help_text"),
        is_active=bool(row.get("is_active", True)),
        show_on_dashboard=bool(row.get("show_on_dashboard", False)),
        category=AssetCategory(row["category"]),
        config=row.get("config"),
        assignment_model=row.get("assignment_model") or "assigned_personal",
        pool_capacity=row.get("pool_capacity"),
        automation_mode=row.get("automation_mode") or "runbook",
        targets=row.get("targets"),
        automation_strategy=row.get("automation_strategy") or "runbook_only",
        composite_steps=row.get("composite_steps"),
        deprovision_policy=row.get("deprovision_policy") or "access_only",
        personal_provisioning_strategy=row.get("personal_provisioning_strategy"),
        naming_pattern=row.get("naming_pattern"),
        max_per_user=int(row.get("max_per_user") or 1),
        monthly_cost=_to_decimal(row.get("monthly_cost")),
        currency=(row.get("currency") or None),
        cost_center=(row.get("cost_center") or None),
        lifecycle_ttl_days=row.get("lifecycle_ttl_days"),
        lifecycle_renewable=bool(row.get("lifecycle_renewable", True)),
        lifecycle_reminder_days=row.get("lifecycle_reminder_days"),
        allow_rdp_users=bool(row.get("allow_rdp_users", False)),
        allow_admin_users=bool(row.get("allow_admin_users", False)),
        rds_gateway_url=row.get("rds_gateway_url"),
        requires_manager_approval=bool(row.get("requires_manager_approval", False)),
        requires_owner_approval=bool(row.get("requires_owner_approval", False)),
        approval_owners=row.get("approval_owners"),
        approval_rules=row.get("approval_rules") or None,
        min_approvals_required=row.get("min_approvals_required"),
        requires_approval_on_modify=bool(row.get("requires_approval_on_modify", False)),
        eligible_requestors_dn=(row.get("eligible_requestors_dn") or None),
        logo=(row.get("logo") or None),
        drift_monitor=bool(row.get("drift_monitor", False)),
    )


@router.post("/import", dependencies=[require_scopes("config:write")])
async def import_config(
    dry_run: bool = Query(
        default=False,
        description="Validate and report what would happen without writing.",
    ),
    doc: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Import asset types + pool instances from a migration document.

    Insert-only: a name that already exists is **skipped**, never
    overwritten (re-importing is safe). ``dry_run=true`` reports the plan
    without touching the database. Pool instances whose ``asset_type``
    name resolves to neither an existing nor a just-imported type are
    skipped.
    """
    incoming_types = doc.get("asset_types") or []
    incoming_pool = doc.get("asset_pool") or []

    existing_type_names = set(
        (await db.execute(select(AssetType.name))).scalars().all()
    )
    existing_pool_names = set(
        (await db.execute(select(AssetPool.name))).scalars().all()
    )

    # ── Phase 1: plan asset types ──────────────────────────────────────
    types_to_create: list[dict[str, Any]] = []
    types_created: list[str] = []
    types_skipped_existing: list[str] = []
    types_invalid: list[dict[str, str]] = []

    for row in incoming_types:
        name = (row or {}).get("name")
        if not name:
            types_invalid.append({"name": "", "reason": "missing name"})
            continue
        if name in existing_type_names:
            types_skipped_existing.append(name)
            continue
        cat = row.get("category")
        if cat not in {c.value for c in AssetCategory}:
            types_invalid.append({"name": name, "reason": f"unknown category {cat!r}"})
            continue
        violations = validate_asset_type(
            category=cat,
            assignment_model=row.get("assignment_model") or "assigned_personal",
            automation_strategy=row.get("automation_strategy") or "runbook_only",
            deprovision_policy=row.get("deprovision_policy") or "access_only",
            personal_provisioning_strategy=row.get("personal_provisioning_strategy"),
        )
        if violations:
            types_invalid.append({
                "name": name,
                "reason": "; ".join(v.message for v in violations),
            })
            continue
        types_to_create.append(row)

    # Names known *after* this import — existing plus to-be-created — so
    # pool instances can bind to a type imported in the same document.
    known_type_names = existing_type_names | {r["name"] for r in types_to_create}

    # ── Phase 2: plan pool instances ───────────────────────────────────
    pool_to_create: list[dict[str, Any]] = []
    pool_skipped_existing: list[str] = []
    pool_skipped_no_type: list[str] = []
    seen_pool_names: set[str] = set()

    for row in incoming_pool:
        name = (row or {}).get("name")
        type_name = (row or {}).get("asset_type")
        if not name:
            continue
        if name in existing_pool_names or name in seen_pool_names:
            pool_skipped_existing.append(name)
            continue
        if type_name not in known_type_names:
            pool_skipped_no_type.append(name)
            continue
        seen_pool_names.add(name)
        pool_to_create.append(row)

    summary = {
        "dry_run": dry_run,
        "asset_types": {
            "created": len(types_to_create),
            "skipped_existing": len(types_skipped_existing),
            "invalid": len(types_invalid),
            "invalid_detail": types_invalid,
        },
        "asset_pool": {
            "created": len(pool_to_create),
            "skipped_existing": len(pool_skipped_existing),
            "skipped_no_type": len(pool_skipped_no_type),
            "skipped_no_type_detail": pool_skipped_no_type,
        },
    }

    if dry_run:
        return summary

    # ── Commit phase: insert types, then pool ──────────────────────────
    for row in types_to_create:
        at = _build_asset_type(row)
        db.add(at)
        await db.flush()
        types_created.append(at.name)
        await aaudit(
            db, "asset_type", at.id, "created",
            new={"name": at.name, "via": "migration_import"},
            by="migration:import",
        )

    # Resolve type ids fresh (covers pre-existing + just-inserted).
    type_id_by_name = dict(
        (await db.execute(text("SELECT name, id FROM asset_types"))).fetchall()
    )
    pool_created = 0
    for row in pool_to_create:
        type_id = type_id_by_name.get(row.get("asset_type"))
        if type_id is None:  # defensive; planned set already checked this
            continue
        db.add(AssetPool(
            name=row["name"],
            asset_type_id=type_id,
            status=AssetStatus.FREE,
            asset_metadata=row.get("metadata"),
        ))
        pool_created += 1

    await db.commit()
    logger.info(
        "admin: migration import created %d asset_type(s), %d pool instance(s)",
        len(types_created), pool_created,
    )
    summary["asset_types"]["created"] = len(types_created)
    summary["asset_pool"]["created"] = pool_created
    return summary
