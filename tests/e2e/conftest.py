"""Shared fixtures for the host-side Playwright E2E smoke suite.

These tests run on the HOST (or CI runner), not inside the containers —
they only speak HTTP to the running stack, so they need no app imports.
Bring the stack up first (`docker compose up -d`) and point the tests at
it via ``IPSOLIS_BASE_URL`` (default ``http://localhost:8000``).

pytest-playwright already provides the ``page`` fixture and the
``--headed`` / ``--slowmo`` / ``--tracing`` CLI flags; we only override
``base_url`` (so ``page.goto("/ui/login")`` resolves), expose the admin
key, and provide a ``logged_in_page`` fixture that lands on the
dashboard.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

# Deterministic first-run credentials — only used when the DB has no
# admin_users yet (fresh CI stack). 12+ chars to satisfy the setup
# password policy in routes/admin_auth.py.
SETUP_USER = "e2e-admin"
SETUP_PASSWORD = "e2e-smoke-password-123"


@pytest.fixture(scope="session")
def base_url() -> str:
    """Root URL of the running ip·Solis stack.

    Overrides pytest-playwright's ``base_url`` fixture so relative
    navigations (``page.goto("/ui/login")``) and API calls
    (``page.request.get("/health")``) work against the compose stack.
    """
    return os.environ.get("IPSOLIS_BASE_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture(scope="session")
def admin_api_key() -> str:
    """Legacy ``ADMIN_API_KEY`` — used for the back-compat admin login path.

    Read from the environment so the value never lands in git. Locally
    this is the same key as in your ``.env``; in CI it's injected by the
    workflow.
    """
    key = os.environ.get("ADMIN_API_KEY", "")
    if not key:
        pytest.skip("ADMIN_API_KEY not set — export it to run the login journey")
    return key


def login_admin(page: Page, base_url: str, admin_api_key: str) -> None:
    """Log into the admin UI, handling both DB states.

    * first-run (empty ``admin_users``) → drive the "Create superadmin"
      setup form (fresh CI stack).
    * normal → legacy back-compat login (empty username + key as
      password; see routes/admin_auth.py).

    Leaves ``page`` on the dashboard (``/ui/``).
    """
    page.goto("/ui/login")
    setup_button = page.get_by_role("button", name="Create superadmin")
    if setup_button.count() > 0:
        page.get_by_label("Username").fill(SETUP_USER)
        page.get_by_label("Password", exact=True).fill(SETUP_PASSWORD)
        page.get_by_label("Confirm password").fill(SETUP_PASSWORD)
        setup_button.click()
    else:
        page.get_by_label("Password", exact=True).fill(admin_api_key)
        page.get_by_role("button", name="Sign in").click()
    # Both paths redirect to an authenticated page — normal login lands on the
    # dashboard, first-run setup now lands on the guided setup wizard
    # (routes/admin_auth.py). Wait until we've left the login page (auth
    # completed + session cookie set), then normalise to the dashboard.
    page.wait_for_url(lambda url: "/ui/login" not in url)
    page.goto(f"{base_url}/ui/")
    expect(page).to_have_url(f"{base_url}/ui/")


@pytest.fixture
def logged_in_page(page: Page, base_url: str, admin_api_key: str) -> Page:
    """A ``page`` already authenticated into the admin UI, on the dashboard."""
    login_admin(page, base_url, admin_api_key)
    return page
