"""HR leaver events — bulk revoke of every active order for a departing user.

Drives ``POST /hr/leaver`` (the unified leaver signal) end-to-end: a bearer
token with the ``hr:leaver`` scope pushes a ``{email}`` leaver event, and the
flow revokes *all* of that user's active orders at once — asserted via the
returned summary, a real entra grant removed on the **mock Graph**, and
idempotency on a second call. Auth is exercised too (missing / wrong scope).

graph.* is pointed at the mock for the test and restored after.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from conftest import NS, BASE_URL

_GID = "ZZ-LEAVER-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]
LEAVER = f"{NS}.leaver@xenpool.de"


def _wait_order(api, oid, timeout=45):
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


def _order(api, atid, email):
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Leaver", "asset_type_id": atid,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    return o["id"]


@pytest.fixture(scope="module")
def leaver_env(api, db, mock):
    """graph→mock; an entra type + a no-target type; one active order each for
    the same leaver user (two active orders total). Restores graph.* after."""
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

    st, entra_t = api.post("/admin/asset-types", json={
        "name": f"{NS}-leaver-entra", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, entra_t
    st, flat_t = api.post("/admin/asset-types", json={
        "name": f"{NS}-leaver-flat", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only"})
    assert st == 201, flat_t

    mock.reset()
    # two active orders for the leaver (distinct types → per-user limit ok)
    o1 = _order(api, entra_t["id"], LEAVER)
    o2 = _order(api, flat_t["id"], LEAVER)
    assert _wait_order(api, o1).get("status") in ("provisioned", "delivered")
    assert _wait_order(api, o2).get("status") in ("provisioned", "delivered")

    yield {"o1": o1, "o2": o2}

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


@pytest.fixture
def hr_token(api):
    st, t = api.post("/admin/api-tokens", json={"name": f"{NS}-hr", "scopes": ["hr:leaver"]})
    assert st == 201, t
    yield t["raw_token"]
    api.delete(f"/admin/api-tokens/{t['id']}")


def _leaver_post(token, email):
    return requests.post(
        BASE_URL + "/hr/leaver",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"event": "leaver", "email": email}, timeout=30)


def test_leaver_revokes_all_active_orders(api, mock, hr_token, leaver_env):
    r = _leaver_post(hr_token, LEAVER)
    assert r.status_code == 200, r.text
    assert r.json().get("orders_revoked") == 2, r.json()

    # the entra grant was actually pulled on the mock
    for _ in range(40):
        removes = [i for i in mock.recent("/graph/members", 50)
                   if i["query"].get("op") == "remove" and i["query"].get("group") == _GID]
        if removes:
            break
        time.sleep(1)
    assert removes, "leaver did not remove the entra grant on the mock"

    # neither order is in the active set any more
    for oid in (leaver_env["o1"], leaver_env["o2"]):
        _, od = api.get(f"/orders/{oid}")
        assert od.get("status") not in ("provisioned", "delivered"), od


def test_leaver_is_idempotent(hr_token, leaver_env):
    # everything was already revoked by the previous test → nothing left to do
    r = _leaver_post(hr_token, LEAVER)
    assert r.status_code == 200, r.text
    assert r.json().get("orders_revoked") == 0, r.json()


def test_leaver_requires_hr_scope(api):
    # a token without hr:leaver → 403
    st, t = api.post("/admin/api-tokens", json={"name": f"{NS}-hr-noscope", "scopes": ["orders:read"]})
    assert st == 201, t
    try:
        r = _leaver_post(t["raw_token"], f"{NS}.nobody@xenpool.de")
        assert r.status_code == 403, r.text
    finally:
        api.delete(f"/admin/api-tokens/{t['id']}")

    # no auth at all → 401
    r = requests.post(BASE_URL + "/hr/leaver", json={"event": "leaver", "email": LEAVER}, timeout=30)
    assert r.status_code == 401, r.text
