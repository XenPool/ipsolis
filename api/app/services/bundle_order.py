"""Bundle ordering — create one OrderGroup + N Order line items.

**Self-contained on purpose**: it reuses the same *primitives* as
``portal_create_order`` (requester-attr freeze, rule/classification/delegation
approver resolution, quorum check, runbook dispatch) but does NOT call into the
portal route, so the proven single-order path stays untouched (see the
Order-groups descope note in TASKS.md).

Per item it runs the standard approval computation; an item with no required
approvals dispatches immediately, otherwise it parks in ``pending_approval`` and
the normal ``send_approval_requests`` task notifies approvers. Items the user
already actively holds are skipped (idempotency). Differences vs the portal
path, deliberately simpler for the bundle context: no A2 manager-self-approval,
no future-dated scheduling, and an invalid rule-approver skips that one approver
(rather than failing the whole order) so one bad rule can't block onboarding.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from celery import Celery
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.approval import OrderApproval
from app.models.asset import AssetType
from app.models.bundle import Bundle
from app.models.order import Order, OrderAction, OrderStatus
from app.models.order_group import OrderGroup
from app.services.onboarding import resolve_bundle_items
from app.utils.audit import _order_snap, aaudit, classify_for_asset_type_id

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 365


async def _build_bundle_approvals(
    db: AsyncSession, order: Order, asset_type: AssetType, recipient_email: str
) -> list[OrderApproval]:
    """Create the required OrderApproval rows for one bundle item (self-contained)."""
    from app.utils.ad_lookup import lookup_manager, lookup_user
    from app.utils.approval_delegation import resolve_active_delegate
    from app.utils.approval_rules import build_context, evaluate_rules
    from app.utils.classification_routing import (
        classification_approvers,
        load_classification_policy,
    )

    created: list[OrderApproval] = []
    seen: set[str] = set()

    async def mk(atype: str, email: str, name: str, **kw: Any) -> OrderApproval:
        d = await resolve_active_delegate(db, email)
        e = d.delegate_email if d else email
        n = (d.delegate_name or d.delegate_email) if d else name
        row = OrderApproval(order_id=order.id, approver_type=atype, approver_email=e, approver_name=n, **kw)
        db.add(row)
        return row

    if asset_type.requires_manager_approval:
        mgr = await asyncio.to_thread(lookup_manager, recipient_email)
        m = mgr.get("manager") if mgr.get("success") else None
        if m and m.get("email"):
            created.append(await mk("manager", m["email"], m.get("display_name") or m["email"]))
            seen.add(m["email"].lower())

    if asset_type.requires_owner_approval and asset_type.approval_owners:
        for o in asset_type.approval_owners:
            if not o.get("email") or o["email"].lower() in seen:
                continue
            created.append(await mk("application_owner", o["email"], o.get("name", o["email"])))
            seen.add(o["email"].lower())

    for ra in evaluate_rules(asset_type.approval_rules, build_context(order, asset_type)):
        if ra["email"].lower() in seen:
            continue
        adr = await asyncio.to_thread(lookup_user, ra["email"])
        if not adr.get("success"):
            logger.warning("bundle: rule %r names invalid approver %s — skipping that approver",
                           ra.get("rule_name"), ra["email"])
            continue
        created.append(await mk(
            "rule:" + ra["rule_name"][:24], adr["email"], adr["display_name"],
            rule_name=ra["rule_name"], rule_threshold=ra.get("rule_threshold"),
            sod_exempt=ra.get("sod_exempt", False),
        ))
        seen.add(adr["email"].lower())

    policy = await load_classification_policy(db)
    for ca in classification_approvers(asset_type, policy):
        if ca["email"].lower() in seen:
            continue
        created.append(await mk(ca["policy"], ca["email"], ca["name"]))
        seen.add(ca["email"].lower())

    await db.flush()
    return created


async def order_bundle(
    db: AsyncSession,
    *,
    bundle: Bundle,
    recipient_email: str,
    recipient_name: str | None,
    requester_email: str | None,
    requester_name: str | None,
    origin: str,
    actor: str,
) -> dict[str, Any]:
    """Order a bundle for a recipient: one OrderGroup + one Order per resolvable
    position. Returns a summary of ordered + skipped items.
    """
    from app.utils.ad_lookup import snapshot_requester_attrs

    resolved = await resolve_bundle_items(db, bundle.id, recipient_email)
    to_order = [i for i in resolved["items"] if i["skip"] is None]
    skipped = [i for i in resolved["items"] if i["skip"] is not None]

    if not to_order:
        return {"group_id": None, "bundle_name": bundle.name, "ordered": [], "skipped": skipped}

    group = OrderGroup(
        origin=origin,
        requester_email=requester_email, requester_name=requester_name,
        recipient_email=recipient_email, recipient_name=recipient_name,
        bundle_id=bundle.id, bundle_name=bundle.name,
        snapshot={"positions": resolved["items"]},
    )
    db.add(group)
    await db.flush()

    requester_attrs = await asyncio.to_thread(snapshot_requester_attrs, recipient_email)
    now = datetime.now(timezone.utc)
    created: list[dict[str, Any]] = []
    dispatch: list[tuple[int, Any]] = []
    approval_orders: list[int] = []

    for item in to_order:
        at = await db.get(AssetType, item["asset_type_id"])
        if not at:
            continue
        until = now + timedelta(days=at.lifecycle_ttl_days or _DEFAULT_TTL_DAYS)
        order = Order(
            user_email=recipient_email, user_name=recipient_name or recipient_email,
            owner_email=recipient_email, owner_name=recipient_name,
            asset_type_id=at.id, rdp_users=[], admin_users=[],
            requested_from=now, requested_until=until,
            action=OrderAction.PROVISION, status=OrderStatus.PENDING,
            config=item.get("default_config"), order_group_id=group.id,
            **requester_attrs,
        )
        db.add(order)
        await db.flush()

        approvals = await _build_bundle_approvals(db, order, at, recipient_email)
        if approvals:
            from app.utils.approval_decision import _compute_bucket_state
            if _compute_bucket_state(approvals, at).all_met:
                for a in approvals:
                    if a.status == "pending":
                        a.status = "superseded"
                        a.decided_at = now
                order.status = OrderStatus.PROCESSING
                dispatch.append((order.id, order.action))
            else:
                order.status = OrderStatus.PENDING_APPROVAL
                approval_orders.append(order.id)
        else:
            order.status = OrderStatus.PROCESSING
            dispatch.append((order.id, order.action))

        await aaudit(
            db, "order", order.id, "created", new=_order_snap(order),
            by=actor, classification=await classify_for_asset_type_id(db, at.id),
        )
        created.append({
            "order_id": order.id, "asset_type_name": item["asset_type_name"],
            "status": order.status.value,
        })

    await aaudit(
        db, "order_group", group.id, "created",
        new={"bundle": bundle.name, "origin": origin, "items": len(created),
             "recipient": recipient_email},
        by=actor,
    )
    await db.commit()

    # Post-commit: dispatch immediate items + notify approvers for the rest.
    from app.routes.webhook import _dispatch_runbook
    for oid, action in dispatch:
        try:
            tid = _dispatch_runbook(oid, action)
            await db.execute(text("UPDATE orders SET celery_task_id = :t WHERE id = :i"),
                             {"t": tid, "i": oid})
        except Exception as exc:  # noqa: BLE001
            logger.warning("bundle: dispatch failed for order %s: %s", oid, exc)
    if approval_orders:
        celery_app = Celery(broker=settings.CELERY_BROKER_URL)
        for oid in approval_orders:
            celery_app.send_task(
                "tasks.workflows.dynamic_runner.send_approval_requests",
                args=[oid], queue="provision",
            )
    await db.commit()

    return {
        "group_id": group.id, "bundle_name": bundle.name,
        "ordered": created, "skipped": skipped,
    }
