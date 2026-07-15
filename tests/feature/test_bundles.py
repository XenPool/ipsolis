"""Onboarding bundles + assignment rules — CRUD, rule eval, idempotency."""
import pytest

from conftest import NS


@pytest.fixture
def asset_type_id(query):
    """Any existing active asset type id to use as a bundle position."""
    rows = query("SELECT id FROM asset_types WHERE is_active = true ORDER BY id LIMIT 1")
    if not rows:
        pytest.skip("no active asset types to build a bundle from")
    return rows[0][0]


def test_bundle_crud_and_rule_eval(api, asset_type_id):
    # create a bundle with one position
    st, b = api.post("/admin/bundles", json={
        "name": f"{NS}-eng-pkg", "description": "eng onboarding",
        "positions": [{"asset_type_id": asset_type_id, "required": True, "sort_order": 0}],
    })
    assert st == 201, b
    bid = b["id"]
    assert b["position_count"] == 1

    # a rule: department == Engineering → bundle
    st, r = api.post("/admin/assignment-rules", json={
        "name": f"{NS}-eng-rule", "bundle_id": bid,
        "condition": {"op": "and", "clauses": [
            {"field": "attr.department", "op": "==", "value": "Engineering"}]},
    })
    assert st == 201, r

    # evaluate a matching user (manual attrs, no AD)
    st, ev = api.post("/admin/onboarding/evaluate",
                      json={"attrs": {"department": "Engineering"}})
    assert st == 200, ev
    names = [m["bundle_name"] for m in ev["matched_bundles"]]
    assert f"{NS}-eng-pkg" in names

    # non-matching user → no match
    st, ev2 = api.post("/admin/onboarding/evaluate", json={"attrs": {"department": "Sales"}})
    assert st == 200
    assert f"{NS}-eng-pkg" not in [m["bundle_name"] for m in ev2["matched_bundles"]]

    api.delete(f"/admin/bundles/{bid}")  # cascades rule + positions


def test_idempotency_skips_held_asset_type(api, asset_type_id, query):
    """A user who already actively holds the asset type is skipped (would-not-order)."""
    # find a user with an active order for asset_type_id
    rows = query(
        "SELECT lower(user_email) FROM orders WHERE asset_type_id=%s "
        "AND status::text IN ('provisioned','delivered','processing','pending_approval') LIMIT 1",
        (asset_type_id,))
    if not rows:
        pytest.skip("no user actively holding the chosen asset type")
    held_email = rows[0][0]

    st, b = api.post("/admin/bundles", json={
        "name": f"{NS}-idem-pkg",
        "positions": [{"asset_type_id": asset_type_id, "required": True}]})
    assert st == 201
    bid = b["id"]
    st, r = api.post("/admin/assignment-rules", json={
        "name": f"{NS}-idem-rule", "bundle_id": bid,
        "condition": {"op": "and", "clauses": []}})  # always matches
    assert st == 201

    st, ev = api.post("/admin/onboarding/evaluate", json={"user_email": held_email})
    assert st == 200
    pkg = next(m for m in ev["matched_bundles"] if m["bundle_name"] == f"{NS}-idem-pkg")
    item = next(i for i in pkg["items"] if i["asset_type_id"] == asset_type_id)
    assert item["skip"] == "already_held"

    api.delete(f"/admin/bundles/{bid}")
