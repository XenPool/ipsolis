"""Smoke: an operator can reach the admin dashboard through the UI.

The login page renders one of two forms depending on DB state (see
routes/admin_auth.py): a first-run "Create superadmin" setup form when
``admin_users`` is empty (fresh CI stack), or the normal "Sign in" form
otherwise (local, where the legacy ``ADMIN_API_KEY`` path is used). The
``logged_in_page`` fixture handles both; here we assert the destination.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_admin_reaches_dashboard(logged_in_page: Page, base_url: str) -> None:
    page = logged_in_page
    expect(page).to_have_url(f"{base_url}/ui/")
    # base.html: {% block title %}Dashboard – {{ app_title }}{% endblock %}
    expect(page).to_have_title(re.compile(r"Dashboard"))
    # A stable dashboard element (the orders drill-down link).
    expect(page.get_by_role("link", name=re.compile("View all orders"))).to_be_visible()
