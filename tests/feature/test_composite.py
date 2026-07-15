"""Composite order (dynamic_runner) — GROUP_TARGETS + RUNBOOK in one order.

A composite asset type runs its access targets *and* an asset-bound runbook in
the configured order. This drives the full chain: POST /orders → dynamic_runner
composite → (1) entra_group grant on the **mock Graph**, (2) an asset-bound
runbook step (self-contained pwsh). The order only reaches ``provisioned`` when
every composite step succeeds, and both effects are asserted independently.

graph.* is pointed at the testlab mock for the test and restored after.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-COMPOSITE-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]
OK_SCRIPT = 'Write-Output \'{"success": true, "marker": "zz-composite-runbook"}\'\n'


@pytest.fixture(scope="module")
def composite_setup(api, db, mock):
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

    # self-contained runbook script module
    st, mod = api.post("/admin/script-modules",
                       json={"name": f"{NS}-composite-mod", "script_content": OK_SCRIPT})
    assert st == 201, mod

    # composite asset type: entra grant first, then the runbook
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-composite-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "composite",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}],
        "composite_steps": [{"type": "GROUP_TARGETS", "order": 1},
                            {"type": "RUNBOOK", "order": 2}],
    })
    assert st == 201, at

    # asset-bound runbook for (type, provision) with one step
    st, rb = api.post("/admin/runbooks", json={
        "name": f"{NS}-composite-rb", "asset_type_id": at["id"], "action": "provision"})
    assert st == 201, rb
    st, s = api.post(f"/admin/runbooks/{rb['id']}/steps", json={
        "position": 1, "step_name": "zz-composite-step", "script_module_id": mod["id"]})
    assert st == 201, s

    yield {"atid": at["id"]}

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def _wait_order(api, oid, timeout=60):
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


def test_composite_order_runs_targets_and_runbook(api, mock, db, query, composite_setup):
    mock.reset()
    email = f"{NS}.composite@xenpool.de"
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Composite",
        "asset_type_id": composite_setup["atid"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    od = _wait_order(api, o["id"])
    assert od.get("status") in ("provisioned", "delivered"), od

    # (1) GROUP_TARGETS: entra member-add landed on the mock
    adds = [i for i in mock.recent("/graph/members", 50)
            if i["query"].get("op") == "add" and i["query"].get("group") == _GID]
    assert adds, "composite GROUP_TARGETS step did not grant the entra group"

    # (2) RUNBOOK: the asset-bound runbook step executed and was recorded
    rows = query(
        "SELECT status FROM order_steps WHERE order_id=%s AND step_name=%s",
        (o["id"], "zz-composite-step"))
    assert rows, "composite RUNBOOK step was not recorded in order_steps"
