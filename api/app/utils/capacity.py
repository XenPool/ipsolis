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

    Caller is responsible for guarding on assignment_model — for
    dedicated_shared the limit is meaningless (one shared instance).
    """
    if not max_per_user or max_per_user < 1:
        return  # disabled / unbounded
    normalized = (user_email or "").strip().lower()
    if not normalized:
        return
    result = await db.execute(
        select(func.count())
        .select_from(Order)
        .where(
            Order.asset_type_id == asset_type_id,
            func.lower(Order.user_email) == normalized,
            Order.status.in_(_ACTIVE_STATUSES),
        )
    )
    current: int = result.scalar_one()
    if current >= max_per_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Per-user limit reached: {user_email} already holds "
                f"{current}/{max_per_user} instances of this asset definition."
            ),
        )
