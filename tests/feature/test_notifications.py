"""Slack delivery against the testlab mock-receiver.

Uses the admin `POST /config/slack/test` endpoint, which posts a real Block Kit
message through the configured webhook. We point the webhook at the mock for the
test and restore the prior config after. Teams is a *real* integration in this
DEV instance and is deliberately left untouched.
"""
import json

import pytest

from conftest import NS

_SLACK_KEYS = ["slack.mode", "slack.webhook_url"]


@pytest.fixture
def slack_to_mock(api, db, mock):
    prev = {}
    with db.cursor() as cur:
        for k in _SLACK_KEYS:
            cur.execute("SELECT value FROM app_config WHERE key=%s", (k,))
            row = cur.fetchone()
            prev[k] = row[0] if row else None
    api.put("/admin/config/slack.mode", json={"value": "enabled"})
    api.put("/admin/config/slack.webhook_url",
            json={"value": "http://host.docker.internal:9000/slack"})
    yield
    for k in _SLACK_KEYS:
        api.put(f"/admin/config/{k}", json={"value": prev.get(k) or ""})


def test_slack_test_message_reaches_mock(api, mock, slack_to_mock):
    mock.reset()
    st, d = api.post("/admin/config/slack/test")
    assert st == 200, d
    assert d.get("ok") is True, d

    items = mock.recent("/slack", 10)
    assert items, "no Slack message reached the mock-receiver"
    payload = json.loads(items[-1]["body"])
    # Block Kit message with a test-notification banner
    assert "blocks" in payload
    assert "Test notification" in payload.get("text", "")
