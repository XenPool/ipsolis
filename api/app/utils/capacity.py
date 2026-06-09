"""Pre-flight capacity checks: pool-wide and per-user quotas."""
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus

# Statuses that count as "still holding a slot". Includes pre-execution states
# (PENDING_APPROVAL, SCHEDULED) so users can't bypass quotas by stacking
# scheduled / awaiting-approval orders.
_ACTIVE_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.PENDING_APPROVAL,
    OrderStatus.SCHEDULED,
    OrderStatus.PROCESSING,
    OrderStatus.PROVISIONING,
    OrderStatus.PROVISIONED,
    OrderStatus.DELIVERED,
)


async def enforce_pool_capacity(
    db: AsyncSession,
    asset_type_id: int,
    pool_capacity: int,
) -> None:
    """Raise HTTP 409 if the pool for asset_type_id is at or over capacity.

    Counts all orders in active (non-terminal) statuses. Only call this for
    capacity_pooled asset types — the caller must guard on assignment_model.
    """
    result = await db.execute(
        select(func.count())
        .select_from(Order)
        .where(
            Order.asset_type_id == asset_type_id,
            Order.status.in_(_ACTIVE_STATUSES),
        )
    )
    current: int = result.scalar_one()
    if current >= pool_capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pool capacity reached: {current}/{pool_capacity} slots in use.",
        )


async def enforce_max_per_user(
    db: AsyncSession,
    asset_type_id: int,
    user_email: str,
    max_per_user: int,
) -> None:
    """Raise HTTP 409 if user_email already holds max_per_user active orders
    of asset_type_id.

    Caller is responsible for guarding on assignment_model.
"""
    if not max_per_user or max_per_user < 1:
        return  # disabled / unbounded
    normalized = (user_email or "").strip().lower()
    if not normalized:
        return
    rows = (
        await db.execute(
            select(Order.id, Order.status)
            .where(
                Order.asset_type_id == asset_type_id,
                func.lower(Order.user_email) == normalized,
                Order.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(Order.id)
        )
    ).all()
    current = len(rows)
    if current >= max_per_user:
        # Bucket the blocking orders so the message tells the user exactly
        # what's eating their quota and which order ID(s) to act on.
        awaiting = [r for r in rows if r.status == OrderStatus.PENDING_APPROVAL]
        in_flight = [r for r in rows if r.status in (
            OrderStatus.SCHEDULED,
            OrderStatus.PENDING,
            OrderStatus.PROCESSING,
            OrderStatus.PROVISIONING,
        )]
        live = [r for r in rows if r.status in (
            OrderStatus.PROVISIONED,
            OrderStatus.DELIVERED,
        )]

        def _ids(rs):
            return ", ".join(f"#{r.id}" for r in rs)

        parts: list[str] = []
        if live:
            parts.append(
                f"{len(live)} active "
                + ("instance" if len(live) == 1 else "instances")
                + f" ({_ids(live)})"
            )
        if awaiting:
            parts.append(
                f"{len(awaiting)} order"
                + ("" if len(awaiting) == 1 else "s")
                + f" awaiting approval ({_ids(awaiting)})"
            )
        if in_flight:
            parts.append(
                f"{len(in_flight)} order"
                + ("" if len(in_flight) == 1 else "s")
                + f" in progress ({_ids(in_flight)})"
            )
        held = " and ".join(parts) if parts else f"{current} order(s)"

        hint_bits: list[str] = []
        if awaiting:
            hint_bits.append(
                f"Ask the approver to decide on {_ids(awaiting)} "
                "(or cancel it)"
            )
        elif in_flight or live:
            hint_bits.append("Wait for an existing one to be released")
        hint = (" " + ". ".join(hint_bits) + ".") if hint_bits else ""

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Per-user limit reached ({current}/{max_per_user}) for this "
                f"asset definition: {user_email} already has {held}.{hint}"
            ),
        )
