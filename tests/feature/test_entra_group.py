"""Entra (Microsoft Graph) group grant through a real order, against the mock.

Drives the full provisioning chain — POST /orders → worker → target_executor
→ graph_client → mock-Graph — and asserts the member-add landed on the mock.
graph.* config is pointed at the testlab mock for the test and restored after,
so real Graph credentials (if any) are never used or clobbered.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-ENTRA-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]


@pytest.fixture(scope="module")
def entra_setup(api, db, mock):
    """Point graph.* at the mock and create an entra_group asset type. Restores
    every graph.* key to its prior value on teardown."""
    prev = {}
    with db.cursor() as cur:
        for k in _GRAPH_KEYS:
            cur.execute("SELECT value FROM app_config WHERE key=%s", (k,))
            row = cur.fetchone()
            prev[k] = row[0] if row else None

    mock_vals = {
        "graph.tenant_id": "mock-tenant",
        "graph.client_id": "mock-client",
        "graph.client_secret": "mock-secret",
        "graph.base_url": "http://host.docker.internal:9000/graph/v1.0",
        "graph.token_url": "http://host.docker.internal:9000/graph/token",
    }
    for k, v in mock_vals.items():
        api.put(f"/admin/config/{k}", json={"value": v})

    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-entra-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}],
    })
    assert st == 201, at

    yield {"atid": at["id"]}

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def _wait_order(api, oid, timeout=40):
    import time
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


def _member_ops(mock, op):
    return [i for i in mock.recent("/graph/members", 50)
            if i["query"].get("op") == op and i["query"].get("group") == _GID]


def _wait_member_op(mock, op, timeout=40):
    """Poll the mock until a group member ``op`` (add/remove) lands, or timeout."""
    import time
    for _ in range(timeout):
        ops = _member_ops(mock, op)
        if ops:
            return ops
        time.sleep(1)
    return []


def test_entra_grant_via_order(api, mock, entra_setup):
    mock.reset()
    email = f"{NS}.entra@xenpool.de"
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Entra",
        "asset_type_id": entra_setup["atid"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat(),
    })
    assert st == 201, o
    od = _wait_order(api, o["id"])

    adds = _member_ops(mock, "add")
    assert adds, f"no entra grant recorded on the mock (order status={od.get('status')})"
    assert od.get("status") in ("provisioned", "delivered"), od


def test_entra_revoke_via_delete_order(api, mock, entra_setup):
    """Delete a provisioned entra order → worker revokes → member-remove on Graph.

    Asserts the full revoke chain (DELETE /orders → delete runbook →
    target_executor → graph_client → mock Graph) lands a ``remove`` op for the
    same group + user that was added. The order status is not polled: the
    cancel route flips it to ``cancelled`` (terminal) before the worker finishes
    revoking, so the mock op is the reliable signal.
    """
    mock.reset()
    email = f"{NS}.entra-rev@xenpool.de"
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Entra Rev",
        "asset_type_id": entra_setup["atid"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat(),
    })
    assert st == 201, o
    _wait_order(api, o["id"])
    adds = _wait_member_op(mock, "add")
    assert adds, "grant did not land on the mock — cannot test revoke"
    added_uid = adds[0]["query"].get("user")

    # Delete the provisioned order → dispatches the delete/revoke runbook.
    st, _ = api.delete(f"/orders/{o['id']}")
    assert st == 204

    removes = _wait_member_op(mock, "remove")
    assert removes, "no entra revoke (member-remove) recorded on the mock"
    # same principal that was added is the one removed (deterministic mock uid)
    assert any(r["query"].get("user") == added_uid for r in removes), removes
