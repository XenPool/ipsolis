"""Stuck-revoke recovery Beat (recover_stuck_revoking).

If a deprovision task dies before any step starts, its order is left in
``revoking``/``delete`` with no running step and access never gets pulled. This
resilience Beat re-dispatches those orders. Driven by provisioning an order
(which records the grant) and then inserting a fresh step-less
``revoking``/``delete`` order for the same user + type — the shape of a
delete order whose Celery task was lost. Running the Beat in the worker
re-dispatches it, the deprovision completes, and the entra grant is removed on
the **mock Graph**.

(Note: order_steps is append-only, so a *provisioned* order keeps its
``running`` step rows forever — the Beat's "no running step" guard only ever
matches a delete order that never got past dispatch, which is what we build.)

graph.* → mock, restored on teardown. The lab has no other stuck orders, so the
instance-wide scan is safe.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-RECOVER-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]
USER = f"{NS}.recover@xenpool.de"


def _wait_order(api, oid, timeout=45):
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


@pytest.fixture(scope="module")
def provisioned_order(api, db, mock):
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
        "name": f"{NS}-recover-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, at

    mock.reset()
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": USER, "user_name": "ZZ Recover", "asset_type_id": at["id"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    assert _wait_order(api, o["id"]).get("status") in ("provisioned", "delivered")

    yield {"atid": at["id"]}

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def test_recover_redispatches_stuck_revoke(api, worker, db, query, mock, provisioned_order):
    atid = provisioned_order["atid"]
    # a delete order whose task was lost before any step ran: revoking, no steps
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO orders "
            "(user_email, user_name, asset_type_id, rdp_users, admin_users, "
            " requested_from, requested_until, action, status, created_at, updated_at) "
            "VALUES (%s, 'ZZ Recover', %s, '{}'::varchar[], '{}'::varchar[], "
            " NOW(), NOW()+INTERVAL '1 day', 'delete'::order_action, "
            " 'revoking'::order_status, NOW(), NOW()) RETURNING id",
            (USER, atid))
        oid = cur.fetchone()[0]

    out = worker.run(
        "import json\n"
        "from tasks.workflows.dynamic_runner import recover_stuck_revoking\n"
        "print('RESULT='+json.dumps(recover_stuck_revoking()))\n")
    import json
    assert json.loads(out).get("recovered", 0) >= 1

    # the re-dispatched deprovision completes: order revoked + entra grant pulled
    for _ in range(45):
        st = query("SELECT status::text FROM orders WHERE id=%s", (oid,))[0][0]
        if st in ("revoked", "failed"):
            break
        time.sleep(1)
    assert st == "revoked", st

    removes = [i for i in mock.recent("/graph/members", 50)
               if i["query"].get("op") == "remove" and i["query"].get("group") == _GID]
    assert removes, "recovered deprovision did not pull the entra grant"
