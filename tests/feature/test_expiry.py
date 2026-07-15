"""Expiry / reclaim Beat path (``check_expiring_assets``).

Seeds a busy pool asset whose ``expires_at`` is in the past, runs the Beat task
synchronously inside the worker, and asserts the reclaim *scheduling* logic: the
original provision order flips to ``expired`` and a fresh ``delete`` order is
created for the same asset (the downstream deprovision runbook itself is covered
by the Slice-4 revoke tests). Uses a no-target group_only type so the delete the
Beat dispatches is a near-no-op.
"""
import pytest

from conftest import NS


@pytest.fixture
def expired_asset(api, db):
    """A group_only asset type + a busy pool asset past its expiry, tied to a
    provisioned order. Returns (asset_type_id, order_id, asset_id)."""
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-expiry-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only"})
    assert st == 201, at
    atid = at["id"]

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO orders "
            "(user_email, user_name, asset_type_id, rdp_users, admin_users, "
            " requested_from, requested_until, action, status, created_at, updated_at) "
            "VALUES (%s, 'ZZ Expiry', %s, '{}'::varchar[], '{}'::varchar[], "
            " NOW()-INTERVAL '2 days', NOW()-INTERVAL '1 hour', "
            " 'provision'::order_action, 'provisioned'::order_status, NOW(), NOW()) "
            "RETURNING id",
            (f"{NS}.expiry@xenpool.de", atid))
        oid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO asset_pool "
            "(name, asset_type_id, status, current_order_id, expires_at) "
            "VALUES (%s, %s, 'busy'::asset_status, %s, NOW()-INTERVAL '1 hour') "
            "RETURNING id",
            (f"{NS}-expiry-asset", atid, oid))
        aid = cur.fetchone()[0]
        # asset_pool references the order; make the order point back at the asset
        cur.execute("UPDATE orders SET assigned_asset_id=%s WHERE id=%s", (aid, oid))
    return atid, oid, aid


def test_expiry_reclaims_expired_asset(worker, db, query, expired_asset):
    atid, oid, aid = expired_asset

    out = worker.run(
        "import json\n"
        "from tasks.workflows.dynamic_runner import check_expiring_assets\n"
        "print('RESULT='+json.dumps(check_expiring_assets()))\n")
    import json
    result = json.loads(out)
    assert result.get("reclaimed", 0) >= 1, result

    # the original provision order is now marked expired
    assert query("SELECT status::text FROM orders WHERE id=%s", (oid,))[0][0] == "expired"

    # a fresh delete order was created for the same asset
    rows = query(
        "SELECT id FROM orders WHERE asset_type_id=%s AND action='delete' "
        "AND assigned_asset_id=%s AND id<>%s", (atid, aid, oid))
    assert rows, "no delete/reclaim order created by check_expiring_assets"
