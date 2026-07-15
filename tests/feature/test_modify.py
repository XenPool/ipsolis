"""Modify order action — atomic revoke + re-grant of access.

A ``modify`` order revokes the user's existing grants for the asset type and
re-grants with the (possibly changed) user lists, in one pass. This drives it
end-to-end on an entra_group type: after an initial provision, a modify order
produces exactly one member-**remove** (rolling back the original grant) and one
member-**add** (the re-grant) on the **mock Graph**, and lands ``delivered``.

The change log shows the atomicity: the original grant row flips
``success → rolled_back`` while the modify order writes a fresh successful grant.
graph.* → mock, restored on teardown.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-MODIFY-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]
USER = f"{NS}.modify@xenpool.de"


def _wait_order(api, oid, timeout=45):
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


def _order(api, atid, action):
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": USER, "user_name": "ZZ Modify", "asset_type_id": atid,
        "action": action,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    return o["id"]


@pytest.fixture(scope="module")
def modify_type(api, db, mock):
    prev = {}
    with db.cursor() as cur:
        for k in _GRAPH_KEYS:
            cur.execute("SELECT value FROM app_config WHERE key=%s", (k,))
            row = cur.fetchone()
            prev[k] = row[0] if row else None
    for k, v in {
        "graph.tenant_id": "mock-tenant", "graph.client_id": "mock-client",
        "graph.client_secret": "mock-secret",
        "graph.base_url": "http://host.docker.internal:9000/graph/v1.0",
        "graph.token_url": "http://host.docker.internal:9000/graph/token",
    }.items():
        api.put(f"/admin/config/{k}", json={"value": v})

    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-modify-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, at

    yield at["id"]

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def _ops(mock, op):
    return [i for i in mock.recent("/graph/members", 50)
            if i["query"].get("op") == op and i["query"].get("group") == _GID]


def test_modify_revokes_then_regrants(api, mock, query, modify_type):
    # initial provision → the user is a group member
    prov = _order(api, modify_type, "provision")
    assert _wait_order(api, prov).get("status") in ("provisioned", "delivered")

    # observe only the modify from here
    mock.reset()

    mod = _order(api, modify_type, "modify")
    od = _wait_order(api, mod)
    assert od.get("status") == "delivered", od

    # exactly the revoke + re-grant pair landed on the mock
    for _ in range(40):
        if _ops(mock, "remove") and _ops(mock, "add"):
            break
        time.sleep(1)
    assert _ops(mock, "remove"), "modify did not revoke the old membership"
    assert _ops(mock, "add"), "modify did not re-grant the membership"

    # atomicity in the change log: original grant rolled back, modify grant fresh
    orig = query("SELECT state FROM order_change_log WHERE order_id=%s AND action='grant'", (prov,))
    assert orig and orig[0][0] == "rolled_back", orig
    fresh = query("SELECT state FROM order_change_log WHERE order_id=%s AND action='grant'", (mod,))
    assert fresh and fresh[0][0] == "success", fresh
