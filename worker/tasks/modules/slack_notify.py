"""Slack notification utilities for the worker.

Mirrors ``api/app/utils/slack_notify.py`` (separate Docker image, separate
deps — cross-image imports aren't supported, so the Block Kit builder is
intentionally duplicated). Delivery is a Slack **incoming webhook** posting a
Block Kit message, the exact parallel of the Teams Adaptive-Card path in
``teams_notify.py``. The signed approval token is channel-agnostic — reused
verbatim from ``teams_notify.make_approval_token`` — so an approve URL works
identically whether it arrives by email, Teams, or Slack.

Slack incoming webhooks can't @mention by email (that needs a Web-API user
lookup), so the message greets the approver by display name only.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


def post_message(webhook_url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """POST a Block Kit message to a Slack incoming-webhook URL.

    Returns ``(success, message)``. Never raises. Slack answers ``ok`` with
    HTTP 200 on success and a plain-text error (e.g. ``invalid_payload``,
    ``no_service``) with a 4xx otherwise.
    """
    if not webhook_url or not webhook_url.strip():
        return False, "Slack webhook URL is not configured."

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            status = resp.status
            text = (resp.read() or b"").decode("utf-8", "replace").strip()
            if 200 <= status < 300:
                return True, f"Posted to Slack (HTTP {status})."
            return False, f"Slack responded with HTTP {status}: {text[:200]}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (e.read() or b"").decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            pass
        return False, f"HTTP {e.code}: {e.reason}{(' — ' + detail) if detail else ''}"
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def build_approval_message(
    *,
    asset_type_name: str,
    requester_name: str,
    requester_email: str,
    approver_name: str,
    review_url: str,
    from_date: str = "",
    until_date: str = "",
    app_title: str = "ip·Solis",
) -> dict[str, Any]:
    """Build a Block Kit payload for an approval request (parallel of the Teams card)."""
    greeting = f"Hi {approver_name}," if approver_name else "Hi,"
    fields = [
        {"type": "mrkdwn", "text": f"*Asset:*\n{asset_type_name or '(unknown)'}"},
        {"type": "mrkdwn", "text": f"*Requester:*\n{requester_name} <{requester_email}>"},
    ]
    if from_date:
        fields.append({"type": "mrkdwn", "text": f"*From:*\n{from_date}"})
    if until_date:
        fields.append({"type": "mrkdwn", "text": f"*Until:*\n{until_date}"})

    headline = f"{app_title} — Access request awaiting approval"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": headline, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": greeting}},
        # Slack caps a section at 10 fields; we never exceed 4.
        {"type": "section", "fields": fields},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Review request", "emoji": True},
                    "url": review_url,
                    "style": "primary",
                }
            ],
        },
    ]
    # ``text`` is the required notification fallback (shown in the push /
    # notification list); ``blocks`` render the rich message in-channel.
    return {"text": headline, "blocks": blocks}
