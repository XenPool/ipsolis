"""SCIM — full filter grammar + joiner/leaver against the live stack."""
import time

import pytest

from conftest import NS, scim_get, scim_send


# ── Filter grammar (RFC 7644 §3.4.2.2) ───────────────────────────────────────

@pytest.fixture
def known_user(query):
    rows = query("SELECT DISTINCT lower(user_email) FROM orders ORDER BY 1 LIMIT 1")
    if not rows:
        pytest.skip("no users (orders) to filter")
    return rows[0][0]


def test_filter_eq_and_grammar(scim_token, known_user):
    # exact eq (fast path)
    r = scim_get(f'/Users?filter=userName eq "{known_user}"', scim_token)
    assert r.status_code == 200
    d = r.json()
    assert d["totalResults"] == 1
    assert d["Resources"][0]["userName"] == known_user

    # co (contains) — general in-memory eval
    frag = known_user.split("@")[0][:3]
    r = scim_get(f'/Users?filter=userName co "{frag}"', scim_token)
    assert r.status_code == 200 and r.json()["totalResults"] >= 1

    # compound and + presence
    r = scim_get(f'/Users?filter=userName sw "{known_user[:2]}" and userName pr', scim_token)
    assert r.status_code == 200 and r.json()["totalResults"] >= 1

    # active eq true (boolean)
    r = scim_get('/Users?filter=active eq true', scim_token)
    assert r.status_code == 200 and r.json()["totalResults"] >= 1


def test_malformed_filter_400(scim_token):
    r = scim_get('/Users?filter=userName xx "a"', scim_token)
    assert r.status_code == 400
    assert r.json().get("scimType") == "invalidFilter"


def test_groups_shim_readonly(scim_token):
    r = scim_get("/Groups", scim_token)
    assert r.status_code == 200 and r.json()["totalResults"] == 0
    r = scim_send("POST", "/Groups", scim_token, {"displayName": "x"})
    assert r.status_code == 501


# ── Joiner / leaver flow ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def joiner_setup(api, db):
    """A zz-test owner-approval asset type + bundle + rule, and scim.joiner_enabled
    flipped on. Owner approval means bundle orders park in pending_approval and
    never dispatch to the worker — keeping the test hermetic. Restores config."""
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-scim-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "requires_owner_approval": True,
        "approval_owners": [{"email": f"{NS}.owner@xenpool.de", "name": "ZZ Owner"}],
    })
    assert st == 201, at
    atid = at["id"]
    st, b = api.post("/admin/bundles", json={
        "name": f"{NS}-scim-bundle",
        "positions": [{"asset_type_id": atid, "required": True}]})
    assert st == 201, b
    st, _ = api.post("/admin/assignment-rules", json={
        "name": f"{NS}-scim-rule", "bundle_id": b["id"],
        "condition": {"op": "and", "clauses": [
            {"field": "attr.department", "op": "==", "value": f"{NS}-eng"}]}})
    assert st == 201

    prev = None
    with db.cursor() as cur:
        cur.execute("SELECT value FROM app_config WHERE key='scim.joiner_enabled'")
        row = cur.fetchone()
        prev = row[0] if row else None
    api.put("/admin/config/scim.joiner_enabled", json={"value": "true"})
    yield {"atid": atid, "bundle_id": b["id"], "email": f"{NS}.joiner@xenpool.de"}
    api.put("/admin/config/scim.joiner_enabled", json={"value": prev or "false"})


def test_joiner_orders_bundle(api, db, scim_token, joiner_setup):
    email = joiner_setup["email"]
    r = scim_send("POST", "/Users", scim_token, {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": email, "displayName": "ZZ Joiner", "active": True,
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": f"{NS}-eng"},
    })
    assert r.status_code in (200, 201), r.text

    # a scim-origin order group with the joiner's item must now exist
    with db.cursor() as cur:
        cur.execute(
            "SELECT og.id, count(o.id) FROM order_groups og "
            "JOIN orders o ON o.order_group_id=og.id "
            "WHERE og.origin='scim' AND lower(og.recipient_email)=lower(%s) "
            "GROUP BY og.id", (email,))
        rows = cur.fetchall()
    assert rows, "SCIM joiner did not create an order group"
    assert rows[0][1] >= 1

    # identity projection persisted with the mapped attribute
    with db.cursor() as cur:
        cur.execute("SELECT attributes->>'department' FROM scim_identities WHERE lower(user_email)=lower(%s)", (email,))
        proj = cur.fetchone()
    assert proj and proj[0] == f"{NS}-eng"


def test_leaver_revokes(api, db, scim_token, joiner_setup):
    email = joiner_setup["email"]
    # ensure the user exists (joiner)
    scim_send("POST", "/Users", scim_token, {
        "userName": email, "displayName": "ZZ Joiner", "active": True,
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": f"{NS}-eng"}})

    r = scim_send("DELETE", f"/Users/{email}", scim_token)
    assert r.status_code in (204, 200), r.text
    time.sleep(1)  # leaver sets status synchronously then dispatches; allow the commit

    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM orders WHERE lower(user_email)=lower(%s) "
                    "AND status::text IN ('pending_approval','provisioned','delivered')", (email,))
        still_active = cur.fetchone()[0]
    assert still_active == 0, "leaver did not revoke the joiner's active orders"
