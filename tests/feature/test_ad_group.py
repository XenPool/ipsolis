"""Real Active Directory: ad_group grant via order + drift detection.

Runs against the testlab DC (``winsrv1.xenpool.local``) — skipped when AD is
not reachable from the worker. Uses an isolated, auto-created zz-test group and
existing test users (john, jupp), and deletes the group on teardown.

Drift is run in **detect_only** mode on purpose: ``reconcile_drift`` scans every
drift-monitored group in the instance, and auto_remediate would mutate real
groups too. detect_only only reads membership + records findings, so it can
never touch a real group. The out-of-band member we inject is removed in
teardown, not by remediation.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

GRP = "CN=zz-test-adgrp,CN=Users,DC=xenpool,DC=local"
# The testlab users carry a real `mail` (@xenpool.de) that Pydantic EmailStr
# accepts and that AD resolves via the mail attribute, so we grant by email —
# the realistic path (principal_source=requester). Membership is asserted by
# sAMAccountName (what list_ad_group_members returns).
MANAGED_EMAIL = "john@xenpool.de"  # granted through ipSolis (an order)
MANAGED_SAM = "john"
OOB_SAM = "jupp"                   # added outside ipSolis (drift)


def _wait_order(api, oid, timeout=45):
    import time
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


@pytest.fixture(scope="module")
def ad_grant(api, ad):
    """A drift-monitored ad_group asset type, with ``john`` granted through an
    order (auto-creates the group). Deletes the group on teardown."""
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-ad-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only", "drift_monitor": True,
        "targets": [{"type": "ad_group", "identifier": GRP,
                     "principal_source": "requester", "create_if_missing": True}],
    })
    assert st == 201, at

    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": MANAGED_EMAIL, "user_name": "John Doe",
        "asset_type_id": at["id"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    od = _wait_order(api, o["id"])
    assert od.get("status") in ("provisioned", "delivered"), od

    yield {"atid": at["id"], "order_id": o["id"]}

    try:
        ad.delete_group(GRP)  # best effort — removes the group + its memberships
    except Exception:  # noqa: BLE001
        pass


def test_ad_group_grant_via_order(ad, ad_grant):
    """The full order → worker → target_executor → real AD chain granted john."""
    assert MANAGED_SAM in ad.members(GRP)


REV_GRP = "CN=zz-test-adrevoke,CN=Users,DC=xenpool,DC=local"
REV_EMAIL = "stefan@xenpool.de"  # granted then revoked through ipSolis
REV_SAM = "stefan"


def _wait_absent(ad, group_dn, sam, timeout=45):
    """Poll AD until ``sam`` is no longer a member of the group, or timeout."""
    import time
    for _ in range(timeout):
        if sam not in ad.members(group_dn):
            return True
        time.sleep(1)
    return False


@pytest.fixture(scope="module")
def ad_revoke(api, ad):
    """An access_only ad_group asset type with ``stefan`` granted through an
    order (auto-creates its own isolated group). Deletes the group on teardown.
    Separate group/order from ``ad_grant`` so the delete here can't disturb the
    grant/drift tests."""
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-ad-revoke-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only",
        "targets": [{"type": "ad_group", "identifier": REV_GRP,
                     "principal_source": "requester", "create_if_missing": True}],
    })
    assert st == 201, at

    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": REV_EMAIL, "user_name": "Stefan",
        "asset_type_id": at["id"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    od = _wait_order(api, o["id"])
    assert od.get("status") in ("provisioned", "delivered"), od

    yield {"atid": at["id"], "order_id": o["id"]}

    try:
        ad.delete_group(REV_GRP)
    except Exception:  # noqa: BLE001
        pass


def test_ad_group_revoke_via_delete_order(api, db, query, ad, ad_revoke):
    """Full revoke chain: DELETE a provisioned order → delete runbook →
    target_executor → real DC removes the member (deprovision_policy=access_only).
    Order status isn't polled (the cancel route flips it to ``cancelled`` before
    the worker revokes); AD membership + the change-log revoke event are asserted.
    """
    # precondition: stefan was granted by the order
    assert REV_SAM in ad.members(REV_GRP)

    st, _ = api.delete(f"/orders/{ad_revoke['order_id']}")
    assert st == 204

    assert _wait_absent(ad, REV_GRP, REV_SAM), \
        f"{REV_SAM} still in {REV_GRP} after delete-order revoke"

    # revoke inverts the original grant in place: its change-log row flips
    # state success → rolled_back (target_executor.revoke doesn't add a new row)
    rows = query(
        "SELECT action, state FROM order_change_log "
        "WHERE identifier=%s AND order_id=%s ORDER BY id DESC", (REV_GRP, ad_revoke["order_id"]))
    assert any(a == "grant" and s == "rolled_back" for a, s in rows), rows


def test_drift_detects_out_of_band_member(api, db, query, ad, ad_grant):
    # inject an out-of-band member (added outside ipSolis)
    ad.add_out_of_band(GRP, OOB_SAM)
    assert {MANAGED_SAM, OOB_SAM} <= set(ad.members(GRP))

    # force detect_only so the system-wide scan never remediates a real group
    with db.cursor() as cur:
        cur.execute("SELECT value FROM app_config WHERE key='drift.remediation_mode'")
        row = cur.fetchone()
        prev = row[0] if row else None
    api.put("/admin/config/drift.remediation_mode", json={"value": "detect_only"})
    try:
        result = ad.run_drift()
        assert result.get("out_of_band", 0) >= 1, result

        # a finding for jupp @ our group, direction out_of_band
        rows = query(
            "SELECT direction, lower(principal) FROM drift_findings "
            "WHERE identifier=%s ORDER BY id DESC", (GRP,))
        dirs = {(d, p) for d, p in rows}
        assert any(d == "out_of_band" and OOB_SAM in p for d, p in dirs), rows
        # john is ipSolis-managed (granted by an active order) → never flagged
        assert not any(MANAGED_SAM in p for _, p in dirs), rows

        # detect_only leaves membership untouched
        assert {MANAGED_SAM, OOB_SAM} <= set(ad.members(GRP))
    finally:
        api.put("/admin/config/drift.remediation_mode", json={"value": prev or "detect_only"})
        # remove the out-of-band member we injected (teardown deletes the group too)
        try:
            ad._run(
                "from tasks.modules.db import get_worker_session\n"
                "from tasks.modules.target_executor import _revoke_ad_group\n"
                f"_revoke_ad_group({GRP!r}, {OOB_SAM!r}, get_worker_session())\n"
                "print('RESULT=ok')\n")
        except Exception:  # noqa: BLE001
            pass
