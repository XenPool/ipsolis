"""Point-in-time access report — order_change_log replay.

The report reconstructs the active access set as of any date by replaying the
immutable ``order_change_log`` (latest successful event per principal/target,
kept only when it's a grant). This drives the differential end to end: after an
entra grant lands today, the live report shows it, a report ``as_of`` yesterday
does not, and the CSV export carries the same row. graph.* → mock, restored.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-ACCESS-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]
USER = f"{NS}.access@xenpool.de"


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
def granted_access(api, db, mock):
    """An entra grant recorded in order_change_log today. Returns the asset
    type id. Restores graph.* on teardown."""
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
        "name": f"{NS}-access-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, at

    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": USER, "user_name": "ZZ Access", "asset_type_id": at["id"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    assert _wait_order(api, o["id"]).get("status") in ("provisioned", "delivered")

    yield at["id"]

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def test_live_report_shows_the_grant(api, granted_access):
    st, body = api.get("/admin/access-report", params={"asset_type_id": granted_access})
    assert st == 200, body
    assert body["meta"]["live"] is True
    principals = {r["principal"].lower() for r in body["rows"]}
    assert USER.lower() in principals, body["rows"]


def test_principal_filter(api, granted_access):
    st, body = api.get("/admin/access-report",
                       params={"asset_type_id": granted_access, "principal": f"{NS}.access"})
    assert st == 200, body
    assert body["rows"], "principal substring filter matched nothing"
    assert all(f"{NS}.access" in r["principal"].lower() for r in body["rows"]), body["rows"]


def test_as_of_before_grant_is_empty(api, granted_access):
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    st, body = api.get("/admin/access-report",
                       params={"asset_type_id": granted_access, "as_of": yesterday})
    assert st == 200, body
    assert body["meta"]["live"] is False
    # the grant was recorded today → not present as of yesterday
    assert body["rows"] == [], body["rows"]


def test_csv_export_carries_the_grant(api, granted_access):
    st, text = api.get("/admin/access-report",
                       params={"asset_type_id": granted_access, "fmt": "csv"})
    assert st == 200
    assert isinstance(text, str) and "Principal" in text  # header row
    assert USER.lower() in text.lower()
