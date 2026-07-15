"""ServiceNow inbound webhook — HMAC authentication + order creation.

Drives ``POST /webhook/servicenow`` end-to-end: a correctly signed body creates
an order, a wrong/absent signature is rejected, and a duplicate
``servicenow_ref`` is a 409 (idempotency). The signature is computed exactly
like the app does — ``sha256=HMAC_SHA256(raw_body, WEBHOOK_SECRET_TOKEN)`` — so
this exercises the real ``_verify_hmac`` path, not a mock.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from conftest import BASE_URL, NS, WEBHOOK_TOKEN


@pytest.fixture(scope="module")
def snow_type(api):
    # High max_per_user so the per-user guard never masks the dup-ref 409 we
    # want to assert (the default per-user limit is 1).
    st, at = api.post("/admin/asset-types", json={
        "name": f"{NS}-snow-type", "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        "max_per_user": 100})
    assert st == 201, at
    return at["name"]


def _payload(snow_type, ref, email=None):
    now = datetime.now(timezone.utc)
    return {
        "servicenow_ref": ref,
        "action": "provision",
        "user_email": email or f"{NS}.snow@xenpool.de",
        "user_name": "ZZ Snow",
        "asset_type_name": snow_type,
        "requested_from": now.isoformat(),
        "requested_until": (now + timedelta(days=1)).isoformat(),
    }


def _sign(body: bytes) -> str:
    mac = hmac.new(WEBHOOK_TOKEN.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _send(body: bytes, sig: str | None):
    headers = {"Content-Type": "application/json"}
    if sig is not None:
        headers["X-Hub-Signature-256"] = sig
    return requests.post(BASE_URL + "/webhook/servicenow", data=body, headers=headers, timeout=30)


def test_valid_hmac_creates_order(snow_type):
    body = json.dumps(_payload(snow_type, f"{NS}-RITM-ok")).encode()
    r = _send(body, _sign(body))
    assert r.status_code == 201, r.text
    assert r.json().get("servicenow_ref") == f"{NS}-RITM-ok"


def test_bad_hmac_rejected(snow_type):
    body = json.dumps(_payload(snow_type, f"{NS}-RITM-bad")).encode()
    r = _send(body, "sha256=" + "0" * 64)
    assert r.status_code == 401, r.text


def test_missing_auth_rejected(snow_type):
    body = json.dumps(_payload(snow_type, f"{NS}-RITM-noauth")).encode()
    r = _send(body, None)
    assert r.status_code == 401, r.text


def test_duplicate_ref_conflict(snow_type):
    ref = f"{NS}-RITM-dup"
    body = json.dumps(_payload(snow_type, ref)).encode()
    r1 = _send(body, _sign(body))
    assert r1.status_code == 201, r1.text
    # same ref again → 409 idempotency guard (fresh signature over same body)
    r2 = _send(body, _sign(body))
    assert r2.status_code == 409, r2.text
