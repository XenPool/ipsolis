"""Smoke (negative): a wrong password is rejected, not silently accepted.

Guards the auth boundary — a regression that lets bad credentials
through is exactly the kind of bug a green happy-path test would miss.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


def test_wrong_password_is_rejected(page: Page) -> None:
    page.goto("/ui/login")

    # In first-run state the login form isn't shown (setup form instead) —
    # nothing to reject against, so skip rather than fail.
    if page.get_by_role("button", name="Create superadmin").count() > 0:
        pytest.skip("first-run: no login form to test rejection against")

    page.get_by_label("Password", exact=True).fill("definitely-not-the-admin-key")
    page.get_by_role("button", name="Sign in").click()

    # admin_auth.py re-renders /ui/login (HTTP 401) with this exact banner.
    expect(page.get_by_text("Incorrect username or password.")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/ui/login$"))
