"""Token-based approval + attestation flows against the live stack.

Both exercise the *external*, no-session endpoints (`/approve/{token}`,
`/attestation/{token}`) that email/Teams links point at. The harness mints the
same HMAC token the app does, so we drive the whole link flow without a mailbox.
Rows are inserted directly (the API's `POST /orders/` dispatches immediately and
never parks in `pending_approval`) and cleaned by the session `zz-test` purge.
"""
import json

import pytest
import requests

from conftest import NS, BASE_URL


@pytest.fixture(scope="module")
def zz_type(api):
    """A minimal group-only asset type with no targets — dispatching it is a
    no-op, so approving an order for it touches no external system."""
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-appr-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
    })
    assert st == 201, at
    return at["id"]


def _new_pending_order(db, zz_type, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO orders "
            "(user_email, user_name, asset_type_id, rdp_users, admin_users, "
            " requested_from, requested_until, action, status, created_at, updated_at) "
            "VALUES (%s, 'ZZ Appr', %s, '{}'::varchar[], '{}'::varchar[], "
            " NOW(), NOW()+INTERVAL '1 day', %s::order_action, %s::order_status, NOW(), NOW()) "
            "RETURNING id",
            (email, zz_type, "provision", "pending_approval"))
        return cur.fetchone()[0]


def _new_approval(db, order_id, approver_email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO order_approvals "
            "(order_id, approver_type, approver_email, approver_name, status, created_at) "
            "VALUES (%s, 'application_owner', %s, 'ZZ Owner', 'pending', NOW()) RETURNING id",
            (order_id, approver_email))
        return cur.fetchone()[0]


def test_approve_via_token(db, query, tokens, zz_type):
    email = f"{NS}.appr-ok@xenpool.de"
    oid = _new_pending_order(db, zz_type, email)
    aid = _new_approval(db, oid, f"{NS}.owner@xenpool.de")

    r = requests.post(f"{BASE_URL}/approve/{tokens.approval(aid)}",
                      data={"decision": "approve"}, timeout=20)
    assert r.status_code == 200, r.text

    # the approval row is decided, and the order advanced out of pending_approval
    assert query("SELECT status FROM order_approvals WHERE id=%s", (aid,))[0][0] == "approved"
    assert query("SELECT status::text FROM orders WHERE id=%s", (oid,))[0][0] != "pending_approval"


def test_decline_via_token_rejects_order(db, query, tokens, zz_type):
    email = f"{NS}.appr-no@xenpool.de"
    oid = _new_pending_order(db, zz_type, email)
    aid = _new_approval(db, oid, f"{NS}.owner@xenpool.de")

    r = requests.post(f"{BASE_URL}/approve/{tokens.approval(aid)}",
                      data={"decision": "reject", "comment": "zz-test decline"}, timeout=20)
    assert r.status_code == 200, r.text

    assert query("SELECT status FROM order_approvals WHERE id=%s", (aid,))[0][0] == "declined"
    assert query("SELECT status::text FROM orders WHERE id=%s", (oid,))[0][0] == "rejected"


def test_bad_token_rejected():
    r = requests.post(f"{BASE_URL}/approve/not-a-real-token",
                      data={"decision": "approve"}, timeout=20)
    assert r.status_code == 410


def test_attestation_handover_ack(db, query, tokens, zz_type):
    """A handover artifact is acknowledged via its signed link."""
    email = f"{NS}.handover@xenpool.de"
    snap = json.dumps({"asset_type_name": f"{NS}-appr-type", "user_email": email})
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO attestation_artifacts "
            "(kind, asset_type_id, recipient_email, recipient_name, status, snapshot, created_at) "
            "VALUES ('handover', %s, %s, 'ZZ Handover', 'pending', %s::json, NOW()) RETURNING id",
            (zz_type, email, snap))
        fid = cur.fetchone()[0]

    tok = tokens.attestation(fid)
    assert requests.get(f"{BASE_URL}/attestation/{tok}", timeout=15).status_code == 200
    r = requests.post(f"{BASE_URL}/attestation/{tok}",
                      data={"acknowledger_name": "ZZ Handover"}, timeout=15)
    assert r.status_code == 200, r.text
    assert query("SELECT status FROM attestation_artifacts WHERE id=%s", (fid,))[0][0] == "acknowledged"
