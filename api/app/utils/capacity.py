"""Pre-flight pool capacity check for capacity_pooled asset types."""
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus

_ACTIVE_STATUSES = (
    OrderStatus.PENDING,
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
