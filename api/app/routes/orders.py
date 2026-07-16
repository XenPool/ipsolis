import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.asset import AssetType, AssignmentModel
from app.models.order import Order, OrderAction, OrderStatus
from app.schemas.order import OrderCreate, OrderRead, OrderUpdate
from app.utils.ad_lookup import snapshot_requester_attrs
from app.utils.audit import _order_snap, aaudit, actor_by, classify_for_asset_type_id
from app.utils.auth import attribute_actor_if_present
from app.utils.capacity import enforce_max_per_user, enforce_pool_capacity
from app.utils.tier import enforce_user_tier_limit

logger = logging.getLogger(__name__)
# Public-by-design router: order creation accepts unauthenticated calls
# (the portal hits /portal/orders/new instead, but external integrations
# like ServiceNow can POST here with no auth — they sign at /webhook).
# The ``attribute_actor_if_present`` dependency is non-raising: it
# captures actor metadata when a caller *does* send X-Admin-Key or a
# Bearer token, leaving anonymous calls unchanged.
router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(attribute_actor_if_present)],
)


@router.get("/", response_model=list[OrderRead])
async def list_orders(
    user_email: str | None = None,
    status_filter: OrderStatus | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[Order]:
    """Returns all orders (optionally filtered)."""
    query = select(Order).options(selectinload(Order.steps))

    if user_email:
        query = query.where(Order.user_email == user_email)
    if status_filter:
        query = query.where(Order.status == status_filter)

    query = query.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
) -> Order:
    """Returns a single order with all steps."""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.steps))
        .where(Order.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )
    return order


@router.post("/", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    request: Request,
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
) -> Order:
    """
    Erstellt eine neue Bestellung via Self-Service-Portal.
    (For ServiceNow webhooks: POST /webhook/servicenow)
    """
    # Pre-flight capacity check — PROVISION only
    if payload.action == OrderAction.PROVISION:
        at_result = await db.execute(
            select(AssetType).where(AssetType.id == payload.asset_type_id)
        )
        asset_type = at_result.scalar_one_or_none()
        if not asset_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown asset_type_id: {payload.asset_type_id}",
            )
        if (
            asset_type.assignment_model == AssignmentModel.CAPACITY_POOLED
            and asset_type.pool_capacity is not None
        ):
            await enforce_pool_capacity(db, asset_type.id, asset_type.pool_capacity)

        await enforce_max_per_user(
            db, asset_type.id, str(payload.user_email), asset_type.max_per_user
        )

        # Free-tier / commercial-band user-count limit — blocks only NEW
        # (not-yet-counted) identities once at/over the effective limit.
        await enforce_user_tier_limit(db, str(payload.user_email))

    # Best-effort AD snapshot — chargeback report needs requester HR
    # attributes for non-portal-driven orders too. lookup_user is sync;
    # offload to a thread so we don't block the asyncio loop.
    import asyncio as _asyncio
    requester_attrs = await _asyncio.to_thread(
        snapshot_requester_attrs, str(payload.user_email)
    )

    order = Order(
        user_email=str(payload.user_email),
        user_name=payload.user_name,
        owner_email=str(payload.owner_email) if payload.owner_email else None,
        owner_name=payload.owner_name,
        snow_req=payload.snow_req,
        asset_type_id=payload.asset_type_id,
        rdp_users=payload.rdp_users,
        admin_users=payload.admin_users,
        requested_from=payload.requested_from,
        requested_until=payload.requested_until,
        action=payload.action,
        status=OrderStatus.PENDING,
        config=payload.config,
        justification=(payload.justification or None),
        **requester_attrs,
    )
    db.add(order)
    await db.flush()

    # Capture action before commit (ORM objects are expired after commit)
    _order_id = order.id
    _action = order.action
    order.status = OrderStatus.PROCESSING

    await aaudit(
        db, "order", order.id, "created", new=_order_snap(order),
        by=actor_by(request, "create_order"),
        classification=await classify_for_asset_type_id(db, order.asset_type_id),
    )
    # Commit FIRST so the row is visible to the worker before dispatch
    await db.commit()

    # Dispatch Celery task after commit
    from app.routes.webhook import _dispatch_runbook
    task_id = _dispatch_runbook(_order_id, _action)
    await db.execute(
        text("UPDATE orders SET celery_task_id = :t WHERE id = :id"),
        {"t": task_id, "id": _order_id},
    )
    await db.commit()

    # Re-fetch with relationships to avoid async lazy-load error
    result = await db.execute(
        select(Order).options(selectinload(Order.steps)).where(Order.id == order.id)
    )
    order = result.scalar_one()

    logger.info("Order created: id=%s user=%s action=%s", order.id, order.user_email, order.action)
    return order


@router.patch("/{order_id}", response_model=OrderRead)
async def update_order(
    request: Request,
    order_id: int,
    payload: OrderUpdate,
    db: AsyncSession = Depends(get_db),
) -> Order:
    """Updates an existing order (e.g. user change, extension)."""
    result = await db.execute(
        select(Order).options(selectinload(Order.steps)).where(Order.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )

    old_snap = _order_snap(order)

    if payload.rdp_users is not None:
        order.rdp_users = payload.rdp_users
    if payload.admin_users is not None:
        order.admin_users = payload.admin_users
    if payload.requested_until is not None:
        order.requested_until = payload.requested_until
    if payload.status is not None:
        order.status = payload.status
    if payload.error_message is not None:
        order.error_message = payload.error_message

    await aaudit(
        db, "order", order.id, "updated", old=old_snap, new=_order_snap(order),
        by=actor_by(request, "update_order"),
        classification=await classify_for_asset_type_id(db, order.asset_type_id),
    )
    await db.commit()
    await db.refresh(order)
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Marks an order as cancelled (no physical deletion)."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )

    old_status = order.status.value

    needs_runbook = order.status in (OrderStatus.DELIVERED, OrderStatus.PROCESSING, OrderStatus.PROVISIONED)
    _order_id = order.id

    order.status = OrderStatus.CANCELLED
    if needs_runbook:
        # Bei PROCESSING: Reclaim-Runbook triggern
        from app.models.order import OrderAction
        order.action = OrderAction.DELETE

    await aaudit(
        db, "order", order.id, "status_changed",
        old={"status": old_status}, new={"status": "cancelled"},
        by=actor_by(request, "cancel_order"),
        classification=await classify_for_asset_type_id(db, order.asset_type_id),
    )
    # Commit FIRST so the row is visible to the worker before dispatch
    await db.commit()

    if needs_runbook:
        from app.routes.webhook import _dispatch_runbook
        _dispatch_runbook(_order_id, "delete")
