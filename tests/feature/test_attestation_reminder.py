"""Attestation overdue-ack reminder Beat (check_overdue_handovers).

Slice 8 covered emission + ack; this covers the nudge for handovers left
unacknowledged past ``attestation.handover_reminder_days``. A pending handover
whose ``created_at`` is older than the window gets one reminder (stamped via
``last_reminder_at``); a second tick dedups. Revocation certs are never nudged.

Opt-in flag + window are saved and restored. The lab has no other artifacts, so
the instance-wide scan is safe; one real reminder email is sent (real SMTP).
"""
import json

import pytest

from conftest import NS


@pytest.fixture
def overdue_handover(api, db):
    """A pending handover artifact emitted 10 days ago (past the 3-day window),
    with the reminder flag on. Restores the flag + window on teardown."""
    prev = {}
    with db.cursor() as cur:
        for k in ("attestation.handover_reminder_enabled", "attestation.handover_reminder_days"):
            cur.execute("SELECT value FROM app_config WHERE key=%s", (k,))
            row = cur.fetchone()
            prev[k] = row[0] if row else None

    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-attrem-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only"})
    assert st == 201, at

    email = f"{NS}.attrem@xenpool.de"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO attestation_artifacts "
            "(kind, asset_type_id, recipient_email, recipient_name, status, snapshot, created_at) "
            "VALUES ('handover', %s, %s, 'ZZ AttRem', 'pending', %s::json, NOW()-INTERVAL '10 days') "
            "RETURNING id",
            (at["id"], email, json.dumps({"asset_type_name": f"{NS}-attrem-type"})))
        fid = cur.fetchone()[0]

    api.put("/admin/config/attestation.handover_reminder_enabled", json={"value": "true"})
    api.put("/admin/config/attestation.handover_reminder_days", json={"value": "3"})

    yield fid

    api.put("/admin/config/attestation.handover_reminder_enabled",
            json={"value": prev.get("attestation.handover_reminder_enabled") or "false"})
    api.put("/admin/config/attestation.handover_reminder_days",
            json={"value": prev.get("attestation.handover_reminder_days") or "3"})


def _run_beat(worker):
    out = worker.run(
        "import json\n"
        "from tasks.workflows.attestation_reminders import check_overdue_handovers\n"
        "print('RESULT='+json.dumps(check_overdue_handovers()))\n")
    return json.loads(out)


def _reminded_at(query, fid):
    return query("SELECT last_reminder_at FROM attestation_artifacts WHERE id=%s", (fid,))[0][0]


def test_overdue_handover_reminded_once_then_dedups(worker, query, overdue_handover):
    fid = overdue_handover
    assert _reminded_at(query, fid) is None

    first = _run_beat(worker)
    assert first.get("due", 0) >= 1, first
    stamped = _reminded_at(query, fid)
    assert stamped is not None, "reminder did not stamp last_reminder_at"

    # a second tick must not re-nudge (now within the window since last_reminder_at)
    _run_beat(worker)
    assert _reminded_at(query, fid) == stamped
