"""Scheduled (future-dated) orders — check_scheduled_orders Beat.

A future-dated order parks as ``scheduled`` and is only dispatched once its
start date arrives. This drives the Beat directly (run in the worker): a
``scheduled`` order whose ``requested_from`` is still in the future is left
untouched, while one whose start has passed is transitioned to ``processing``
and provisioned — asserted via the entra grant landing on the **mock Graph**.

Scheduled orders are normally created by the portal's future-date branch; here
they're inserted directly so the Beat's due-filter is what's under test.
graph.* → mock, restored on teardown.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-SCHED-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]


@pytest.fixture(scope="module")
def sched_type(api, db, mock):
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
        "name": f"{NS}-sched-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, at

    yield at["id"]

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def _insert_scheduled(db, atid, email, from_interval):
    """Insert a status='scheduled' provision order with requested_from = now +
    ``from_interval`` (a controlled SQL interval literal, e.g. "2 days")."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO orders "
            "(user_email, user_name, asset_type_id, rdp_users, admin_users, "
            " requested_from, requested_until, action, status, created_at, updated_at) "
            "VALUES (%s, 'ZZ Sched', %s, '{}'::varchar[], '{}'::varchar[], "
            f" NOW()+INTERVAL '{from_interval}', NOW()+INTERVAL '30 days', "
            " 'provision'::order_action, 'scheduled'::order_status, NOW(), NOW()) "
            "RETURNING id",
            (email, atid))
        return cur.fetchone()[0]


def _run_beat(worker):
    out = worker.run(
        "import json\n"
        "from tasks.workflows.dynamic_runner import check_scheduled_orders\n"
        "print('RESULT='+json.dumps(check_scheduled_orders()))\n")
    return json.loads(out)


def _adds(mock):
    return [i for i in mock.recent("/graph/members", 50)
            if i["query"].get("op") == "add" and i["query"].get("group") == _GID]


def test_future_order_not_dispatched(worker, db, query, mock, sched_type):
    mock.reset()
    oid = _insert_scheduled(db, sched_type, f"{NS}.sched-fut@xenpool.de",
                            "2 days")
    _run_beat(worker)

    # start date hasn't arrived → still scheduled, nothing granted
    assert query("SELECT status::text FROM orders WHERE id=%s", (oid,))[0][0] == "scheduled"
    assert _adds(mock) == [], _adds(mock)


def test_due_order_dispatched_and_provisioned(api, worker, db, query, mock, sched_type):
    mock.reset()
    oid = _insert_scheduled(db, sched_type, f"{NS}.sched-due@xenpool.de",
                            "-1 hour")
    result = _run_beat(worker)
    assert result.get("dispatched", 0) >= 1, result

    # dispatched → provisioned, entra grant landed on the mock
    for _ in range(45):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in ("provisioned", "delivered", "failed"):
            break
        time.sleep(1)
    assert od.get("status") in ("provisioned", "delivered"), od
    assert _adds(mock), "due scheduled order did not grant the entra group"
