"""Admin CRUD for software licenses / contracts (``SoftwareContract``).

The *customer's* vendor software contracts (Adobe CC, Microsoft 365, …) that
back one or more asset types — see the model docstring for why this is kept
separate from the product ``.lic`` licensing.

Reads inherit an ``auditor`` floor (finance/audit can view utilisation);
writes carry an explicit ``admin`` guard. Each list row is enriched with
**live seat consumption** (active orders across every bound asset type) and
the derived Model-A figures (seat price, utilisation, shelfware).
"""
from __future__ import annotations

from datetime import date as _date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.software_contract import (
    BILLING_INTERVALS,
    BILLING_TO_MONTHLY_DIVISOR,
    SoftwareContract,
)
from app.utils.audit import aaudit
from app.utils.auth import require_admin_key, require_scopes
from app.utils.rbac import require_role

router = APIRouter(
    prefix="/admin/contracts",
    tags=["admin-contracts"],
    dependencies=[
        Depends(require_admin_key),
        require_scopes("config:read"),
        require_role("auditor"),
    ],
)

_WRITE_GATE = require_role("admin")

# Same "active" set the cost report / capacity enforcement use.
_ACTIVE_ORDER_STATUSES = (
    "pending", "pending_approval", "scheduled",
    "processing", "provisioning", "provisioned", "delivered",
)


# ── Schemas ─────────────────────────────────────────────────────────────────

class ContractCreate(BaseModel):
    vendor: str = Field(min_length=1, max_length=200)
    product: str = Field(min_length=1, max_length=200)
    contract_value: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    billing_interval: str = "annual"
    licensed_seats: int | None = Field(default=None, ge=0)
    start_date: _date | None = None
    renewal_date: _date | None = None
    notice_period_days: int = Field(default=0, ge=0)
    auto_renew: bool = False
    cost_center: str | None = Field(default=None, max_length=100)
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("billing_interval")
    @classmethod
    def _valid_interval(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in BILLING_INTERVALS:
            raise ValueError(f"billing_interval must be one of {BILLING_INTERVALS}")
        return v


class ContractUpdate(BaseModel):
    vendor: str | None = Field(default=None, min_length=1, max_length=200)
    product: str | None = Field(default=None, min_length=1, max_length=200)
    contract_value: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    billing_interval: str | None = None
    licensed_seats: int | None = Field(default=None, ge=0)
    start_date: _date | None = None
    renewal_date: _date | None = None
    notice_period_days: int | None = Field(default=None, ge=0)
    auto_renew: bool | None = None
    cost_center: str | None = Field(default=None, max_length=100)
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("billing_interval")
    @classmethod
    def _valid_interval(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in BILLING_INTERVALS:
            raise ValueError(f"billing_interval must be one of {BILLING_INTERVALS}")
        return v


# ── Serialisation + Model-A math ────────────────────────────────────────────

def _monthly_value(contract_value: Decimal, billing_interval: str) -> float:
    div = BILLING_TO_MONTHLY_DIVISOR.get(billing_interval, 1) or 1
    return round(float(contract_value) / div, 2)


def _contract_dict(
    c: SoftwareContract, *, consumption: int = 0, bound_type_count: int = 0
) -> dict[str, Any]:
    monthly_value = _monthly_value(c.contract_value, c.billing_interval)
    seats = c.licensed_seats
    seat_price = round(monthly_value / seats, 2) if seats and seats > 0 else None
    # Model A: charge actual consumption; unused seats = shelfware (unrecovered).
    allocated = round((seat_price or 0.0) * consumption, 2) if seat_price is not None else None
    if seat_price is not None and seats:
        shelfware = round(seat_price * max(0, seats - consumption), 2)
        utilization = round(consumption / seats, 4) if seats else None
        over_allocated = consumption > seats
    else:
        shelfware = None
        utilization = None
        over_allocated = False
    days_to_renewal = (c.renewal_date - _date.today()).days if c.renewal_date else None
    return {
        "id": c.id,
        "vendor": c.vendor,
        "product": c.product,
        "contract_value": float(c.contract_value),
        "currency": c.currency,
        "billing_interval": c.billing_interval,
        "monthly_value": monthly_value,
        "licensed_seats": seats,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "renewal_date": c.renewal_date.isoformat() if c.renewal_date else None,
        "notice_period_days": c.notice_period_days,
        "auto_renew": c.auto_renew,
        "cost_center": c.cost_center,
        "notes": c.notes,
        "days_to_renewal": days_to_renewal,
        # Live Model-A figures
        "bound_type_count": bound_type_count,
        "consumption": consumption,
        "seat_price_monthly": seat_price,
        "allocated_monthly": allocated,
        "shelfware_monthly": shelfware,
        "utilization": utilization,
        "over_allocated": over_allocated,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


async def _consumption_map(db: AsyncSession) -> dict[int, int]:
    """contract_id → count of active orders across all bound asset types."""
    rows = (await db.execute(text(
        "SELECT at.contract_id AS cid, COUNT(o.id) AS n "
        "FROM asset_types at "
        "JOIN orders o ON o.asset_type_id = at.id "
        "WHERE at.contract_id IS NOT NULL AND o.status::text = ANY(:st) "
        "GROUP BY at.contract_id"
    ), {"st": list(_ACTIVE_ORDER_STATUSES)})).all()
    return {int(cid): int(n) for cid, n in rows}


async def _bound_count_map(db: AsyncSession) -> dict[int, int]:
    """contract_id → number of asset types bound to it."""
    rows = (await db.execute(text(
        "SELECT contract_id, COUNT(*) FROM asset_types "
        "WHERE contract_id IS NOT NULL GROUP BY contract_id"
    ))).all()
    return {int(cid): int(n) for cid, n in rows}


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_contracts(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    contracts = (await db.execute(
        select(SoftwareContract).order_by(SoftwareContract.vendor, SoftwareContract.product)
    )).scalars().all()
    consumption = await _consumption_map(db)
    bound = await _bound_count_map(db)
    return [
        _contract_dict(c, consumption=consumption.get(c.id, 0), bound_type_count=bound.get(c.id, 0))
        for c in contracts
    ]


@router.get("/{contract_id}")
async def get_contract(contract_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    c = await db.get(SoftwareContract, contract_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    consumption = (await _consumption_map(db)).get(contract_id, 0)
    bound = (await _bound_count_map(db)).get(contract_id, 0)
    out = _contract_dict(c, consumption=consumption, bound_type_count=bound)
    # Include the bound asset types so the detail view can list them.
    out["bound_types"] = [
        {"id": tid, "name": name}
        for tid, name in (await db.execute(text(
            "SELECT id, name FROM asset_types WHERE contract_id = :c ORDER BY name"
        ), {"c": contract_id})).all()
    ]
    return out


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[_WRITE_GATE, require_scopes("config:write")])
async def create_contract(
    payload: ContractCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    c = SoftwareContract(
        vendor=payload.vendor,
        product=payload.product,
        contract_value=payload.contract_value,
        currency=payload.currency,
        billing_interval=payload.billing_interval,
        licensed_seats=payload.licensed_seats,
        start_date=payload.start_date,
        renewal_date=payload.renewal_date,
        notice_period_days=payload.notice_period_days,
        auto_renew=payload.auto_renew,
        cost_center=payload.cost_center or None,
        notes=payload.notes or None,
    )
    db.add(c)
    await db.flush()
    await aaudit(db, "software_contract", c.id, "created",
                 new={"vendor": c.vendor, "product": c.product}, by="api:create_contract")
    await db.commit()
    await db.refresh(c)
    return _contract_dict(c)


@router.put("/{contract_id}", dependencies=[_WRITE_GATE, require_scopes("config:write")])
async def update_contract(
    contract_id: int, payload: ContractUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    c = await db.get(SoftwareContract, contract_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    fields = payload.model_dump(exclude_unset=True)
    renewal_touched = "renewal_date" in fields or "notice_period_days" in fields
    for k, v in fields.items():
        setattr(c, k, v if v != "" else None)
    # Changing the renewal clock re-arms the reminder (clear the dedup stamp).
    if renewal_touched:
        c.last_renewal_reminder_at = None
    await aaudit(db, "software_contract", c.id, "updated",
                 new={k: (str(v) if isinstance(v, (Decimal, _date, datetime)) else v)
                      for k, v in fields.items()},
                 by="api:update_contract")
    await db.commit()
    await db.refresh(c)
    consumption = (await _consumption_map(db)).get(contract_id, 0)
    bound = (await _bound_count_map(db)).get(contract_id, 0)
    return _contract_dict(c, consumption=consumption, bound_type_count=bound)


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None,
               dependencies=[_WRITE_GATE, require_scopes("config:write")])
async def delete_contract(contract_id: int, db: AsyncSession = Depends(get_db)) -> None:
    c = await db.get(SoftwareContract, contract_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
    # Bound asset types are unbound via the FK ON DELETE SET NULL — they
    # simply fall back to their own ``monthly_cost`` in the cost report.
    await aaudit(db, "software_contract", c.id, "deleted",
                 old={"vendor": c.vendor, "product": c.product}, by="api:delete_contract")
    await db.delete(c)
    await db.commit()
