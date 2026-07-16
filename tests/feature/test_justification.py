"""Order justification — opt-in per asset definition, shown to the approver.

Covers the new business-justification field end-to-end at the layers the
harness can drive over HTTP + DB:

* the two asset-type flags (``collect_justification`` / ``justification_required``)
  persist;
* an order carries its ``justification`` (via the /orders API);
* the approval decision page ``GET /approve/{token}`` renders the justification
  to the approver.

The portal-form required-field enforcement (422) lives behind portal auth and is
verified in the browser (see the plan's verification steps), not here.
"""
from datetime import datetime, timedelta, timezone

import pytest
import requests

from conftest import NS, BASE_URL


@pytest.fixture(scope="module")
def just_type(api):
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-just-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "collect_justification": True, "justification_required": True})
    assert st == 201, at
    return at["id"]


def test_flags_persist(query, just_type):
    row = query(
        "SELECT collect_justification, justification_required FROM asset_types WHERE id=%s",
        (just_type,))
    assert row and row[0][0] is True and row[0][1] is True, row


def test_api_order_stores_justification(api, query, just_type):
    now = datetime.now(timezone.utc)
    reason = "zz-test needs this for the Q3 audit"
    st, o = api.post("/orders/", json={
        "user_email": f"{NS}.just@xenpool.de", "user_name": "ZZ Just",
        "asset_type_id": just_type, "justification": reason,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    stored = query("SELECT justification FROM orders WHERE id=%s", (o["id"],))[0][0]
    assert stored == reason, stored


def test_approver_page_shows_justification(db, query, tokens, just_type):
    reason = "zz-visible-reason: replacing a departed colleague"
    email = f"{NS}.just-appr@xenpool.de"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO orders "
            "(user_email, user_name, asset_type_id, rdp_users, admin_users, "
            " requested_from, requested_until, action, status, justification, "
            " created_at, updated_at) "
            "VALUES (%s, 'ZZ Just Appr', %s, '{}'::varchar[], '{}'::varchar[], "
            " NOW(), NOW()+INTERVAL '1 day', 'provision'::order_action, "
            " 'pending_approval'::order_status, %s, NOW(), NOW()) RETURNING id",
            (email, just_type, reason))
        oid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO order_approvals "
            "(order_id, approver_type, approver_email, approver_name, status, created_at) "
            "VALUES (%s, 'application_owner', %s, 'ZZ Owner', 'pending', NOW()) RETURNING id",
            (oid, f"{NS}.just-owner@xenpool.de"))
        aid = cur.fetchone()[0]

    r = requests.get(f"{BASE_URL}/approve/{tokens.approval(aid)}", timeout=15)
    assert r.status_code == 200, r.text
    assert "Justification" in r.text
    assert reason in r.text
