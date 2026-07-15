"""Software-contract cost report — Model-A per-seat math (``by_contract``).

Binds a software contract to an asset type, creates active orders (seat
consumption), and asserts the live ``GET /admin/cost-report`` ``by_contract``
view computes exact seat economics: per-seat monthly price, allocated spend,
unrecovered *shelfware*, utilisation, and seat over-allocation.

No-target group_only types so orders complete without external systems; one
order per user (default ``max_per_user`` is 1).
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS


def _order(api, atid, email):
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Cost", "asset_type_id": atid,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    return o["id"]


def _bind_type(api, contract_id, name):
    st, at = api.post("/admin/asset-types", json={
        "name": name, "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "contract_id": contract_id})
    assert st == 201, at
    return at["id"]


@pytest.fixture(scope="module")
def cost_env(api):
    """Contract A: 72000/yr, 100 seats, 2 active orders (utilised).
    Contract B: 1200/yr, 1 seat, 2 active orders (over-allocated).

    The two seat-holders are **reused** across both contracts (2 distinct
    users, not 4) and their orders are deleted on teardown — the DEV instance
    is on the 25-user free tier and ~14 real users already sit on the budget,
    so the harness must keep its peak active-user count small.
    """
    st, ca = api.post("/admin/contracts", json={
        "vendor": f"{NS}-vendor-a", "product": "ZZ Suite A",
        "contract_value": "72000.00", "currency": "EUR",
        "billing_interval": "annual", "licensed_seats": 100})
    assert st == 201, ca
    st, cb = api.post("/admin/contracts", json={
        "vendor": f"{NS}-vendor-b", "product": "ZZ Suite B",
        "contract_value": "1200.00", "currency": "EUR",
        "billing_interval": "annual", "licensed_seats": 1})
    assert st == 201, cb

    at_a = _bind_type(api, ca["id"], f"{NS}-cost-a")
    at_b = _bind_type(api, cb["id"], f"{NS}-cost-b")
    # two users, each holding a seat on BOTH contracts (distinct asset types →
    # per-user limit ok) → consumption == 2 on each contract, only 2 identities
    oids = []
    for i in range(2):
        u = f"{NS}.cost{i}@xenpool.de"
        oids.append(_order(api, at_a, u))
        oids.append(_order(api, at_b, u))

    yield {"cid_a": ca["id"], "cid_b": cb["id"]}

    # release the seat-holders so later test files keep their user-budget
    for oid in oids:
        api.delete(f"/orders/{oid}")


def _row(api, contract_id):
    st, body = api.get("/admin/cost-report")
    assert st == 200, body
    rows = [r for r in body.get("by_contract", []) if r["contract_id"] == contract_id]
    assert rows, f"contract {contract_id} not in by_contract view"
    return rows[0]


def test_seat_math_utilised_contract(api, cost_env):
    r = _row(api, cost_env["cid_a"])
    # 72000/yr → 6000/mo; 100 seats → 60/seat; 2 consumed
    assert r["monthly_value"] == 6000.0, r
    assert r["seat_price_monthly"] == 60.0, r
    assert r["consumption"] == 2, r
    assert r["allocated_monthly"] == 120.0, r          # 60 × 2
    assert r["shelfware_monthly"] == 5880.0, r         # 60 × (100 − 2)
    assert r["utilization"] == 0.02, r                 # 2 / 100
    assert r["over_allocated"] is False, r


def test_over_allocation_flagged(api, cost_env):
    r = _row(api, cost_env["cid_b"])
    # 1200/yr → 100/mo; 1 seat → 100/seat; 2 consumed > 1 seat
    assert r["seat_price_monthly"] == 100.0, r
    assert r["consumption"] == 2, r
    assert r["allocated_monthly"] == 200.0, r          # 100 × 2
    assert r["shelfware_monthly"] == 0.0, r            # no unused seats
    assert r["over_allocated"] is True, r
