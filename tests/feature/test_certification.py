"""Access-certification campaigns — kickoff + signed-token reviewer decisions.

Drives the certification flow end-to-end against the live stack:

* create a campaign scoped to two zz asset types + start it → one pending
  ``certification_review`` per matching active order (reviewer resolved);
* a reviewer **confirms** via the signed ``/review/{token}`` link → review
  ``confirmed``, access untouched;
* a reviewer **revokes** via the link → review ``revoked``, the order is pulled
  (``REVOKING`` → deprovision runbook) and the entra grant is actually removed
  on the **mock Graph**.

The revoke target is an entra_group so access removal is observable on the mock;
graph.* is pointed at the mock for the test and restored after. The order-status
race on revoke (see the Slice-4 note) means we assert the mock member-remove +
the review row, not a polled order status.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from conftest import NS, BASE_URL

_GID = "ZZ-CERT-GID"
_GRAPH_KEYS = ["graph.tenant_id", "graph.client_id", "graph.client_secret",
               "graph.base_url", "graph.token_url"]


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
        "user_email": email, "user_name": "ZZ Cert", "asset_type_id": atid,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    return o["id"]


@pytest.fixture(scope="module")
def cert_env(api, db, mock):
    """graph→mock, an entra type (revoke target) + a no-target type (confirm
    target), one active order each, and a running campaign scoped to both →
    two pending reviews. Restores graph.* on teardown."""
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
        "name": f"{NS}-cert-entra", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only",
        "targets": [{"type": "entra_group", "identifier": _GID, "principal_source": "requester"}]})
    assert st == 201, entra_t
    st, flat_t = api.post("/admin/asset-types", json={
        "name": f"{NS}-cert-flat", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only"})
    assert st == 201, flat_t

    mock.reset()
    rev_email = f"{NS}.cert-rev@xenpool.de"
    conf_email = f"{NS}.cert-conf@xenpool.de"
    rev_oid = _order(api, entra_t["id"], rev_email)
    conf_oid = _order(api, flat_t["id"], conf_email)
    assert _wait_order(api, rev_oid).get("status") in ("provisioned", "delivered")
    assert _wait_order(api, conf_oid).get("status") in ("provisioned", "delivered")

    # campaign scoped to both types → start creates one review per order
    due = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    st, camp = api.post("/admin/certifications", json={
        "name": f"{NS}-cert-campaign", "due_at": due,
        "scope": {"asset_type_ids": [entra_t["id"], flat_t["id"]]}})
    assert st == 201, camp
    st, started = api.post(f"/admin/certifications/{camp['id']}/start")
    assert st == 200, started
    assert started.get("reviews_created", 0) >= 2, started

    yield {"campaign_id": camp["id"], "rev_oid": rev_oid, "conf_oid": conf_oid,
           "rev_email": rev_email, "conf_email": conf_email}

    for k in _GRAPH_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def _review_for(query, campaign_id, order_id):
    rows = query(
        "SELECT id, status, lower(reviewer_email) FROM certification_reviews "
        "WHERE campaign_id=%s AND order_id=%s", (campaign_id, order_id))
    return rows[0] if rows else None


def test_start_creates_pending_reviews(query, cert_env):
    for oid, email in ((cert_env["rev_oid"], cert_env["rev_email"]),
                       (cert_env["conf_oid"], cert_env["conf_email"])):
        r = _review_for(query, cert_env["campaign_id"], oid)
        assert r, f"no review created for order {oid}"
        assert r[1] == "pending", r
        # no manager/owner on these orders → reviewer falls back to the requester
        assert r[2] == email.lower(), r


def test_token_confirm_keeps_access(api, query, tokens, cert_env):
    r = _review_for(query, cert_env["campaign_id"], cert_env["conf_oid"])
    assert r and r[1] == "pending", r
    tok = tokens.review(r[0])
    assert requests.get(f"{BASE_URL}/review/{tok}", timeout=15).status_code == 200
    resp = requests.post(f"{BASE_URL}/review/{tok}", data={"decision": "confirm"}, timeout=20)
    assert resp.status_code == 200, resp.text

    assert query("SELECT status FROM certification_reviews WHERE id=%s", (r[0],))[0][0] == "confirmed"
    # confirm has no order side-effect — access stays active
    _, od = api.get(f"/orders/{cert_env['conf_oid']}")
    assert od.get("status") in ("provisioned", "delivered"), od


def test_token_revoke_pulls_entra_access(mock, query, tokens, cert_env):
    r = _review_for(query, cert_env["campaign_id"], cert_env["rev_oid"])
    assert r and r[1] == "pending", r
    tok = tokens.review(r[0])
    resp = requests.post(f"{BASE_URL}/review/{tok}", data={"decision": "revoke"}, timeout=20)
    assert resp.status_code == 200, resp.text

    assert query("SELECT status FROM certification_reviews WHERE id=%s", (r[0],))[0][0] == "revoked"

    # the certification revoke pulled the entra grant on the mock
    for _ in range(40):
        removes = [i for i in mock.recent("/graph/members", 50)
                   if i["query"].get("op") == "remove" and i["query"].get("group") == _GID]
        if removes:
            break
        time.sleep(1)
    assert removes, "certification revoke did not remove the entra grant on the mock"
