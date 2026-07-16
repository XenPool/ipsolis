"""Per-asset-type portal step visibility (off / detailed / debug).

Controls how much of an order's execution steps the self-service portal shows
the requester. Covers what the harness can drive over HTTP + DB:

* the default is ``off`` (no step list — status only);
* ``detailed`` / ``debug`` persist and are updatable;
* an invalid value is rejected (400).

The actual portal rendering (off hides the step list; debug adds raw log/error)
is portal-auth-gated and verified in the browser.
"""
import pytest

from conftest import NS


def _mk(api, name, **extra):
    st, at = api.post("/admin/asset-types", json={
        "name": name, "category": "application_access",
        "assignment_model": "capacity_pooled", "automation_strategy": "group_only",
        **extra})
    return st, at


def test_default_is_off(api, query):
    st, at = _mk(api, f"{NS}-vis-default")
    assert st == 201, at
    row = query("SELECT portal_step_visibility FROM asset_types WHERE id=%s", (at["id"],))
    assert row[0][0] == "off", row


def test_debug_persists_and_updates(api, query):
    st, at = _mk(api, f"{NS}-vis-debug", portal_step_visibility="debug")
    assert st == 201, at
    aid = at["id"]
    assert query("SELECT portal_step_visibility FROM asset_types WHERE id=%s", (aid,))[0][0] == "debug"

    st, _ = api.put(f"/admin/asset-types/{aid}", json={"portal_step_visibility": "detailed"})
    assert st == 200
    assert query("SELECT portal_step_visibility FROM asset_types WHERE id=%s", (aid,))[0][0] == "detailed"


def test_invalid_value_rejected(api):
    st, body = _mk(api, f"{NS}-vis-bad", portal_step_visibility="verbose")
    assert st == 400, body
    assert "portal_step_visibility" in str(body)
