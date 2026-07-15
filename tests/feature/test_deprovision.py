"""Deprovision policies on pool-backed (assigned_personal) asset types.

Earlier revoke tests used ``access_only`` (group_only, no pool asset). These
drive the pool-asset lifecycle for the two return-to-pool policies:

* provision reserves a Free pool asset → ``busy`` (current_order_id set);
* deleting a ``return_to_pool`` order releases it back to ``Free``;
* deleting a ``return_to_pool_reinstall`` order flags it ``Reinstall`` (held out
  of the pool until a separate reinstall runbook clears it).

No access targets, so the flow is pure pool-manager state. The order-status race
on delete (Slice-4 note) means we poll the **asset** status, not the order.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS


def _asset_status(query, aid):
    rows = query("SELECT status::text, current_order_id FROM asset_pool WHERE id=%s", (aid,))
    return rows[0] if rows else (None, None)


def _wait_asset_status(query, aid, want, timeout=45):
    for _ in range(timeout):
        st, _coid = _asset_status(query, aid)
        if st == want:
            return True
        time.sleep(1)
    return False


def _wait_order(api, oid, timeout=45):
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


def _make_type_with_asset(api, db, policy, tag):
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-depro-{tag}", "category": "device_access",
        "assignment_model": "assigned_personal", "automation_strategy": "group_only",
        "personal_provisioning_strategy": "assign_existing_free",
        "deprovision_policy": policy})
    assert st == 201, at
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO asset_pool (name, asset_type_id, status) "
            "VALUES (%s, %s, 'Free'::asset_status) RETURNING id",
            (f"{NS}-depro-asset-{tag}", at["id"]))
        aid = cur.fetchone()[0]
    return at["id"], aid


def _provision(api, atid, email):
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Depro", "asset_type_id": atid,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    return o["id"]


def test_return_to_pool_releases_asset(api, db, query):
    atid, aid = _make_type_with_asset(api, db, "return_to_pool", "rtp")
    oid = _provision(api, atid, f"{NS}.depro-rtp@xenpool.de")
    assert _wait_order(api, oid).get("status") in ("provisioned", "delivered")

    # the Free asset was reserved and is now busy on this order
    st, coid = _asset_status(query, aid)
    assert st == "busy", (st, coid)
    assert coid == oid

    # delete → return the asset to the pool
    assert api.delete(f"/orders/{oid}")[0] == 204
    assert _wait_asset_status(query, aid, "Free"), _asset_status(query, aid)
    assert _asset_status(query, aid)[1] is None  # current_order_id cleared


def test_return_to_pool_reinstall_flags_asset(api, db, query):
    atid, aid = _make_type_with_asset(api, db, "return_to_pool_reinstall", "rei")
    oid = _provision(api, atid, f"{NS}.depro-rei@xenpool.de")
    assert _wait_order(api, oid).get("status") in ("provisioned", "delivered")
    assert _asset_status(query, aid)[0] == "busy"

    # delete → asset is held for reinstall, not returned to Free
    assert api.delete(f"/orders/{oid}")[0] == 204
    assert _wait_asset_status(query, aid, "Reinstall"), _asset_status(query, aid)
