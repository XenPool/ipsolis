"""Pre-flight capacity enforcement on order creation (pool-wide + per-user).

Pure HTTP against the live stack — no external systems. Uses no-target
group_only asset types so ordering is a near-no-op; the point is the 409 guard
in ``enforce_pool_capacity`` / ``enforce_max_per_user`` on ``POST /orders/``.
An order in any active status (processing/provisioned/…) holds a slot, so the
second order trips the limit regardless of how fast the first completes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS


def _order(api, atid, email, name="ZZ Cap"):
    now = datetime.now(timezone.utc)
    return api.post("/orders/", json={
        "user_email": email, "user_name": name, "asset_type_id": atid,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})


def test_pool_capacity_blocks_second_order(api):
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-cap-pool", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "pool_capacity": 1})
    assert st == 201, at

    st1, o1 = _order(api, at["id"], f"{NS}.cap1@xenpool.de")
    assert st1 == 201, o1
    # second order for the same (full) pool → 409, capacity message
    st2, o2 = _order(api, at["id"], f"{NS}.cap2@xenpool.de")
    assert st2 == 409, o2
    assert "capacity" in str(o2).lower()


def test_max_per_user_blocks_same_user_only(api):
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-cap-user", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "max_per_user": 1})
    assert st == 201, at

    user = f"{NS}.capuser@xenpool.de"
    st1, o1 = _order(api, at["id"], user)
    assert st1 == 201, o1
    # same user, second active order → 409 per-user limit
    st2, o2 = _order(api, at["id"], user)
    assert st2 == 409, o2
    assert "per-user" in str(o2).lower()
    # a different user is unaffected by the first user's quota
    st3, o3 = _order(api, at["id"], f"{NS}.capother@xenpool.de")
    assert st3 == 201, o3
