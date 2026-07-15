"""Certification auto-revoke Beat (scan_and_remind + auto_revoke_on_overdue).

Slice 9 covered reviewer decisions; this covers the unattended path: a campaign
whose ``due_at`` has passed, with ``certification.auto_revoke_on_overdue`` on,
auto-revokes every still-pending review and pulls the underlying access. Driven
by running ``scan_and_remind`` in the worker against an overdue campaign.

Safe against the system-wide scan: the only other running campaign in a suite
run (test_certification's) is future-dated with no pending reviews, so it is
skipped. The config flag is saved + restored. graph.* → mock, restored too.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NS

_GID = "ZZ-AUTOREV-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]
USER = f"{NS}.autorev@xenpool.de"


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
def overdue_campaign(api, db, mock):
    """A provisioned entra order + a running campaign whose due_at is backdated
    into the past → one pending, overdue review. graph.* + the auto-revoke flag
    are saved and restored."""
    prev = {}
    with db.cursor() as cur:
        for k in _GRAPH_KEYS + ["certification.auto_revoke_on_overdue"]:
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
        "name": f"{NS}-autorev-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, at

    mock.reset()
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": USER, "user_name": "ZZ AutoRev", "asset_type_id": at["id"],
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    assert _wait_order(api, o["id"]).get("status") in ("provisioned", "delivered")

    # campaign must be created with a future due_at, then backdated to overdue
    due = (now + timedelta(days=7)).isoformat()
    st, camp = api.post("/admin/certifications", json={
        "name": f"{NS}-autorev-camp", "due_at": due,
        "scope": {"asset_type_ids": [at["id"]]}})
    assert st == 201, camp
    assert api.post(f"/admin/certifications/{camp['id']}/start")[0] == 200
    with db.cursor() as cur:
        cur.execute("UPDATE certification_campaigns SET due_at = NOW() - INTERVAL '2 days' "
                    "WHERE id = %s", (camp["id"],))

    yield {"campaign_id": camp["id"], "order_id": o["id"]}

    api.put("/admin/config/certification.auto_revoke_on_overdue",
            json={"value": prev.get("certification.auto_revoke_on_overdue") or "false"})
    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def test_overdue_pending_review_auto_revoked(api, worker, db, query, mock, overdue_campaign):
    cid, oid = overdue_campaign["campaign_id"], overdue_campaign["order_id"]
    review = query("SELECT id, status FROM certification_reviews "
                   "WHERE campaign_id=%s AND order_id=%s", (cid, oid))
    assert review and review[0][1] == "pending", review
    rid = review[0][0]

    api.put("/admin/config/certification.auto_revoke_on_overdue", json={"value": "true"})
    out = worker.run(
        "import json\n"
        "from tasks.workflows.certification_reminders import scan_and_remind\n"
        "print('RESULT='+json.dumps(scan_and_remind()))\n")
    assert isinstance(json.loads(out), dict)

    # the pending review was auto-revoked
    assert query("SELECT status FROM certification_reviews WHERE id=%s", (rid,))[0][0] == "auto_revoked"

    # and the underlying entra access was pulled on the mock
    for _ in range(40):
        removes = [i for i in mock.recent("/graph/members", 50)
                   if i["query"].get("op") == "remove" and i["query"].get("group") == _GID]
        if removes:
            break
        time.sleep(1)
    assert removes, "auto-revoke did not pull the entra grant"
