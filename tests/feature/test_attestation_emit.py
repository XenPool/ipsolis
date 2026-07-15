"""Worker-side attestation emission on real order completion.

Slice 2 tested the signed-link ack flow against a hand-inserted artifact row.
This drives the *worker* emitting artifacts for real: an asset type opted into
``requires_handover_ack`` + ``emit_revocation_certificate`` produces

* a **handover** artifact when a provision order reaches ``provisioned`` — then
  acknowledged through its own emitted signed token (emit → ack, full loop);
* a **revocation** certificate when the order is deleted and reaches ``revoked``.

No-target group_only type, so orders complete without touching external systems.
Emission also sends a real SMTP mail to the (zz-test) recipient — the same real
SMTP path every order in this harness already uses; the address is namespaced.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from conftest import NS, BASE_URL


@pytest.fixture(scope="module")
def att_type(api):
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-att-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "deprovision_policy": "access_only",
        "requires_handover_ack": True, "emit_revocation_certificate": True})
    assert st == 201, at
    return at["id"]


def _order(api, atid, email):
    now = datetime.now(timezone.utc)
    st, o = api.post("/orders/", json={
        "user_email": email, "user_name": "ZZ Att", "asset_type_id": atid,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat()})
    assert st == 201, o
    return o["id"]


def _wait_order(api, oid, timeout=45):
    terminal = {"provisioned", "delivered", "failed", "rejected", "cancelled"}
    od = {}
    for _ in range(timeout):
        _, od = api.get(f"/orders/{oid}")
        if od.get("status") in terminal:
            return od
        time.sleep(1)
    return od


def _wait_artifact(query, oid, kind, timeout=30):
    """Poll attestation_artifacts for an emitted (order_id, kind), or timeout."""
    for _ in range(timeout):
        rows = query(
            "SELECT id, status FROM attestation_artifacts "
            "WHERE order_id=%s AND kind=%s ORDER BY id DESC", (oid, kind))
        if rows:
            return rows[0]
        time.sleep(1)
    return None


def test_handover_emitted_and_acknowledged(api, query, tokens, att_type):
    oid = _order(api, att_type, f"{NS}.att-ho@xenpool.de")
    od = _wait_order(api, oid)
    assert od.get("status") in ("provisioned", "delivered"), od

    art = _wait_artifact(query, oid, "handover")
    assert art, "worker did not emit a handover artifact on provisioned"
    fid, status = art
    assert status == "pending", art

    # close the loop: acknowledge via the artifact's own emitted signed token
    tok = tokens.attestation(fid)
    assert requests.get(f"{BASE_URL}/attestation/{tok}", timeout=15).status_code == 200
    r = requests.post(f"{BASE_URL}/attestation/{tok}",
                      data={"acknowledger_name": "ZZ Att"}, timeout=15)
    assert r.status_code == 200, r.text
    assert query("SELECT status FROM attestation_artifacts WHERE id=%s", (fid,))[0][0] == "acknowledged"


def test_revocation_certificate_emitted_on_delete(api, query, att_type):
    oid = _order(api, att_type, f"{NS}.att-rev@xenpool.de")
    od = _wait_order(api, oid)
    assert od.get("status") in ("provisioned", "delivered"), od

    # delete → revoke; order status races cancelled→revoked, so poll the artifact
    st, _ = api.delete(f"/orders/{oid}")
    assert st == 204

    art = _wait_artifact(query, oid, "revocation")
    assert art, "worker did not emit a revocation certificate on revoked"
    assert art[1] == "emitted", art
