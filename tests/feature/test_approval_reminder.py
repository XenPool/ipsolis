"""Approval reminder Beat (approval_reminders.scan_and_remind).

Slice 2 covered the token approve/decline flow; this covers the nudge for
approvals left pending. A pending approval older than
``approval.reminder_after_hours`` gets a reminder (``reminder_count`` bumped,
``last_reminded_at`` stamped); a second tick within the window dedups.

The stale approval is inserted directly and backdated. Flags saved + restored;
the lab has no other stale pending approvals so the instance-wide scan is safe.
"""
import json

import pytest

from conftest import NS


@pytest.fixture
def stale_approval(api, db):
    prev = {}
    with db.cursor() as cur:
        for k in ("approval.reminders_enabled", "approval.reminder_after_hours"):
            cur.execute("SELECT value FROM app_config WHERE key=%s", (k,))
            row = cur.fetchone()
            prev[k] = row[0] if row else None

    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-apprem-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only"})
    assert st == 201, at

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO orders "
            "(user_email, user_name, asset_type_id, rdp_users, admin_users, "
            " requested_from, requested_until, action, status, created_at, updated_at) "
            "VALUES (%s, 'ZZ ApprRem', %s, '{}'::varchar[], '{}'::varchar[], "
            " NOW(), NOW()+INTERVAL '1 day', 'provision'::order_action, "
            " 'pending_approval'::order_status, NOW(), NOW()) RETURNING id",
            (f"{NS}.apprem@xenpool.de", at["id"]))
        oid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO order_approvals "
            "(order_id, approver_type, approver_email, approver_name, status, "
            " reminder_count, created_at) "
            "VALUES (%s, 'application_owner', %s, 'ZZ Owner', 'pending', 0, "
            " NOW()-INTERVAL '48 hours') RETURNING id",
            (oid, f"{NS}.apprem-owner@xenpool.de"))
        aid = cur.fetchone()[0]

    api.put("/admin/config/approval.reminders_enabled", json={"value": "true"})
    api.put("/admin/config/approval.reminder_after_hours", json={"value": "24"})

    yield aid

    api.put("/admin/config/approval.reminders_enabled",
            json={"value": prev.get("approval.reminders_enabled") or "true"})
    api.put("/admin/config/approval.reminder_after_hours",
            json={"value": prev.get("approval.reminder_after_hours") or "24"})


def _run_beat(worker):
    out = worker.run(
        "import json\n"
        "from tasks.workflows.approval_reminders import scan_and_remind\n"
        "print('RESULT='+json.dumps(scan_and_remind()))\n")
    return json.loads(out)


def _count(query, aid):
    return query("SELECT reminder_count, last_reminded_at FROM order_approvals WHERE id=%s", (aid,))[0]


def test_stale_approval_reminded_once_then_dedups(worker, query, stale_approval):
    aid = stale_approval
    assert _count(query, aid)[0] == 0

    _run_beat(worker)
    cnt, stamped = _count(query, aid)
    assert cnt == 1, cnt
    assert stamped is not None, "reminder did not stamp last_reminded_at"

    # a second tick within reminder_after_hours must not re-remind
    _run_beat(worker)
    assert _count(query, aid)[0] == 1
