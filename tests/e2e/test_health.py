"""Smoke: the stack is up and the health endpoint reports its subsystems.

Cheapest possible check — if this fails, the compose stack isn't
serving and every other E2E test would fail too, so run it first.
Uses Playwright's API request context (no browser needed).
"""
from __future__ import annotations

from playwright.sync_api import APIRequestContext


def test_health_endpoint_serves(base_url: str, playwright) -> None:
    ctx: APIRequestContext = playwright.request.new_context(base_url=base_url)
    try:
        resp = ctx.get("/health")
        # /health always returns 200 by design; routing decisions key off
        # the JSON ``status`` field (ok | degraded), not the HTTP code.
        assert resp.status == 200, f"/health returned {resp.status}"
        body = resp.json()
        assert body.get("status") in {"ok", "degraded"}, body
        # DB must at least be reachable for the app to be useful.
        # ``database`` is "ok" | "unavailable" (see routes/health.py).
        assert body.get("database") == "ok", f"database unhealthy: {body}"
    finally:
        ctx.dispose()
