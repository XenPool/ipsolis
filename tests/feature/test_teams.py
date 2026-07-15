"""Real Microsoft Teams delivery (no mock).

Teams is a real, configured integration in this DEV instance (``teams.mode``
enabled + a live Workflow webhook) and is deliberately **never** redirected to
the testlab mock — unlike Slack/Graph. This test posts one real test card via
``POST /admin/config/teams/test`` and asserts the live webhook accepted it
(``ok: true`` — the real 2xx/202 read-back).

Skips cleanly when Teams isn't enabled/configured, so it never fails a lab that
simply hasn't set Teams up.
"""
import pytest


def test_teams_real_delivery(api):
    st, body = api.post("/admin/config/teams/test")
    assert st == 200, body

    ok = body.get("ok")
    if ok is None:
        pytest.skip(f"Teams disabled/unconfigured — nothing delivered: {body.get('message')}")
    assert ok is True, f"Teams webhook rejected the test card: {body.get('message')}"
