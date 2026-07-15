"""Smoke: the self-service portal is mounted and doesn't 500.

Config-agnostic: depending on ``portal.auth_required`` and the provider
registry, ``/portal/`` either renders the catalog or redirects to a
sign-in page — both are fine. We only assert the portal router serves a
non-error response (catches a portal-wide 500 or an unmounted router).
Uses Playwright's API request context (follows redirects, no browser).
"""
from __future__ import annotations

from playwright.sync_api import APIRequestContext


def test_portal_is_reachable(base_url: str, playwright) -> None:
    ctx: APIRequestContext = playwright.request.new_context(base_url=base_url)
    try:
        resp = ctx.get("/portal/")
        # 2xx (catalog or rendered login) — not a 4xx/5xx. A 404 would mean
        # the portal router isn't mounted; a 5xx a server error.
        assert resp.ok, f"/portal/ returned {resp.status}"
    finally:
        ctx.dispose()
