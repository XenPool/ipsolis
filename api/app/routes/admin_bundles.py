"""Admin API: onboarding bundles, assignment rules, and rule evaluation.

Bundles group existing ``AssetType`` positions; assignment rules map a user's
attributes (approval-rule condition format) to a bundle. The evaluate endpoint
resolves a user's AD attributes (with manual override) and previews which
bundles match and which items *would* be ordered (idempotency-aware) — it does
**not** create orders. Reads at ``auditor`` floor, writes gated at ``admin``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.assignment_rule import AssignmentRule
from app.models.bundle import Bundle, BundlePosition
from app.services.onboarding import (
    build_user_context,
    evaluate_assignment_rules,
    resolve_bundle_items,
)
from app.utils.audit import aaudit
from app.utils.auth import require_admin_key
from app.utils.rbac import require_role

router = APIRouter(
    tags=["admin-bundles"],
    dependencies=[Depends(require_admin_key), require_role("auditor")],
)

_WRITE = require_role("admin")


# ── Schemas ─────────────────────────────────────────────────────────────────

class PositionIn(BaseModel):
    asset_type_id: int
    required: bool = True
    sort_order: int = 0
    default_config: dict[str, Any] | None = None


class BundleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    is_active: bool = True
    catalog_visible: bool = True
    positions: list[PositionIn] = Field(default_factory=list)


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    condition: dict[str, Any] | None = None
    bundle_id: int
    is_active: bool = True
    priority: int = 100


class EvaluateIn(BaseModel):
    user_email: str | None = None
    # Manual attribute override / supply (wins over AD-resolved values).
    attrs: dict[str, Any] | None = None


class OrderBundlesIn(BaseModel):
    user_email: str = Field(min_length=3)
    user_name: str | None = None
    bundle_ids: list[int] = Field(min_length=1)


# ── Serialisation ────────────────────────────────────────────────────────────

def _pos_dict(p: BundlePosition, at_names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": p.id, "asset_type_id": p.asset_type_id,
        "asset_type_name": at_names.get(p.asset_type_id, "(unknown)"),
        "required": p.required, "sort_order": p.sort_order,
        "default_config": p.default_config,
    }


async def _bundle_dict(db: AsyncSession, b: Bundle) -> dict[str, Any]:
    positions = (await db.execute(
        select(BundlePosition).where(BundlePosition.bundle_id == b.id)
        .order_by(BundlePosition.sort_order, BundlePosition.id)
    )).scalars().all()
    from app.models.asset import AssetType
    at_ids = [p.asset_type_id for p in positions]
    names: dict[int, str] = {}
    if at_ids:
        for at in (await db.execute(select(AssetType).where(AssetType.id.in_(at_ids)))).scalars().all():
            names[at.id] = at.name
    return {
        "id": b.id, "name": b.name, "description": b.description,
        "is_active": b.is_active, "catalog_visible": b.catalog_visible,
        "positions": [_pos_dict(p, names) for p in positions],
        "position_count": len(positions),
    }


def _rule_dict(r: AssignmentRule) -> dict[str, Any]:
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "condition": r.condition, "bundle_id": r.bundle_id,
        "is_active": r.is_active, "priority": r.priority,
    }


async def _sync_positions(db: AsyncSession, bundle_id: int, positions: list[PositionIn]) -> None:
    # Replace-all: simplest correct semantics for the admin editor.
    await db.execute(
        BundlePosition.__table__.delete().where(BundlePosition.bundle_id == bundle_id)
    )
    for i, p in enumerate(positions):
        db.add(BundlePosition(
            bundle_id=bundle_id, asset_type_id=p.asset_type_id,
            required=p.required, sort_order=p.sort_order if p.sort_order else i,
            default_config=p.default_config,
        ))


# ── Bundles CRUD ─────────────────────────────────────────────────────────────

@router.get("/admin/bundles")
async def list_bundles(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    bundles = (await db.execute(select(Bundle).order_by(Bundle.name))).scalars().all()
    return [await _bundle_dict(db, b) for b in bundles]


@router.get("/admin/bundles/{bundle_id}")
async def get_bundle(bundle_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    b = await db.get(Bundle, bundle_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bundle not found")
    return await _bundle_dict(db, b)


@router.post("/admin/bundles", status_code=status.HTTP_201_CREATED, dependencies=[_WRITE])
async def create_bundle(payload: BundleIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if (await db.execute(select(Bundle).where(Bundle.name == payload.name))).scalars().first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Bundle {payload.name!r} already exists")
    b = Bundle(name=payload.name, description=payload.description,
               is_active=payload.is_active, catalog_visible=payload.catalog_visible)
    db.add(b)
    await db.flush()
    await _sync_positions(db, b.id, payload.positions)
    await aaudit(db, "bundle", b.id, "created", new={"name": b.name}, by="api:create_bundle")
    await db.commit()
    return await _bundle_dict(db, b)


@router.put("/admin/bundles/{bundle_id}", dependencies=[_WRITE])
async def update_bundle(bundle_id: int, payload: BundleIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    b = await db.get(Bundle, bundle_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bundle not found")
    b.name = payload.name
    b.description = payload.description
    b.is_active = payload.is_active
    b.catalog_visible = payload.catalog_visible
    await _sync_positions(db, bundle_id, payload.positions)
    await aaudit(db, "bundle", b.id, "updated", new={"name": b.name}, by="api:update_bundle")
    await db.commit()
    return await _bundle_dict(db, b)


@router.delete("/admin/bundles/{bundle_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None, dependencies=[_WRITE])
async def delete_bundle(bundle_id: int, db: AsyncSession = Depends(get_db)) -> None:
    b = await db.get(Bundle, bundle_id)
    if not b:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bundle not found")
    await aaudit(db, "bundle", b.id, "deleted", old={"name": b.name}, by="api:delete_bundle")
    await db.delete(b)  # positions + rules cascade via FK
    await db.commit()


# ── Assignment rules CRUD ────────────────────────────────────────────────────

@router.get("/admin/assignment-rules")
async def list_rules(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rules = (await db.execute(
        select(AssignmentRule).order_by(AssignmentRule.priority, AssignmentRule.name)
    )).scalars().all()
    return [_rule_dict(r) for r in rules]


@router.post("/admin/assignment-rules", status_code=status.HTTP_201_CREATED, dependencies=[_WRITE])
async def create_rule(payload: RuleIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if not await db.get(Bundle, payload.bundle_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bundle_id does not exist")
    if (await db.execute(select(AssignmentRule).where(AssignmentRule.name == payload.name))).scalars().first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Rule {payload.name!r} already exists")
    r = AssignmentRule(name=payload.name, description=payload.description,
                       condition=payload.condition, bundle_id=payload.bundle_id,
                       is_active=payload.is_active, priority=payload.priority)
    db.add(r)
    await db.flush()
    await aaudit(db, "assignment_rule", r.id, "created", new={"name": r.name}, by="api:create_rule")
    await db.commit()
    return _rule_dict(r)


@router.put("/admin/assignment-rules/{rule_id}", dependencies=[_WRITE])
async def update_rule(rule_id: int, payload: RuleIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    r = await db.get(AssignmentRule, rule_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    if not await db.get(Bundle, payload.bundle_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bundle_id does not exist")
    r.name = payload.name
    r.description = payload.description
    r.condition = payload.condition
    r.bundle_id = payload.bundle_id
    r.is_active = payload.is_active
    r.priority = payload.priority
    await aaudit(db, "assignment_rule", r.id, "updated", new={"name": r.name}, by="api:update_rule")
    await db.commit()
    return _rule_dict(r)


@router.delete("/admin/assignment-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None, dependencies=[_WRITE])
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)) -> None:
    r = await db.get(AssignmentRule, rule_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    await aaudit(db, "assignment_rule", r.id, "deleted", old={"name": r.name}, by="api:delete_rule")
    await db.delete(r)
    await db.commit()


# ── Evaluate (preview only) ──────────────────────────────────────────────────

@router.post("/admin/onboarding/evaluate")
async def evaluate_for_user(
    payload: EvaluateIn = Body(...), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Resolve a user's attributes (AD + manual override) and preview matched
    bundles + resolved items. No orders are created.
    """
    attrs: dict[str, Any] = {}
    ad_ok = False
    if payload.user_email:
        from app.utils.ad_lookup import lookup_user
        res = await asyncio.to_thread(lookup_user, payload.user_email)
        if res.get("success"):
            ad_ok = True
            for k in ("department", "cost_center", "company", "employee_id", "title",
                      "sam_account", "email", "display_name"):
                if res.get(k) is not None:
                    attrs[k] = res[k]
    # Manual override wins.
    if payload.attrs:
        attrs.update({k: v for k, v in payload.attrs.items() if v is not None})

    context = build_user_context(attrs)
    matched = await evaluate_assignment_rules(db, context)
    resolved = []
    for m in matched:
        r = await resolve_bundle_items(db, m["bundle_id"], payload.user_email or attrs.get("email", ""))
        r["matched_rules"] = m["matched_rules"]
        resolved.append(r)
    return {
        "user_email": payload.user_email,
        "ad_resolved": ad_ok,
        "attributes": attrs,
        "context_fields": sorted(context.keys()),
        "matched_bundles": resolved,
    }


@router.post("/admin/onboarding/order", dependencies=[_WRITE])
async def order_bundles_for_user(
    payload: OrderBundlesIn, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Order one or more bundles for a user (admin-triggered onboarding).

    Creates one OrderGroup + N line items per bundle through the self-contained
    bundle-order service (portal path untouched). Items the user already holds
    are skipped. Returns a per-bundle summary of ordered / skipped items.
    """
    from app.services.bundle_order import order_bundle
    from app.utils.audit import actor_by

    # Best-effort display name from AD if not supplied.
    name = (payload.user_name or "").strip()
    if not name:
        res = await asyncio.to_thread(_lookup_user_safe, payload.user_email)
        name = res.get("display_name") or payload.user_email

    actor = actor_by(request, "onboarding_order")
    results = []
    for bid in payload.bundle_ids:
        bundle = await db.get(Bundle, bid)
        if not bundle or not bundle.is_active:
            results.append({"bundle_id": bid, "error": "bundle not found or inactive"})
            continue
        summary = await order_bundle(
            db, bundle=bundle,
            recipient_email=payload.user_email, recipient_name=name,
            requester_email=None, requester_name=actor,
            origin="rule_based", actor=actor,
        )
        results.append(summary)
    return {"user_email": payload.user_email, "results": results}


def _lookup_user_safe(email: str) -> dict[str, Any]:
    try:
        from app.utils.ad_lookup import lookup_user
        return lookup_user(email) or {}
    except Exception:  # noqa: BLE001
        return {}
