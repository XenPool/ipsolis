"""Smoke: the core admin pages render without server errors.

Catches the most common regression — a broken template or route that
500s — across the pages an operator uses daily. Read-only: navigates and
asserts each page's title/heading, changes nothing.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect

# (path, expected word in <title>). Every listed template has a
# {% block title %} — see api/app/templates/*.html.
_PAGES = [
    ("/ui/", "Dashboard"),
    ("/ui/orders", "Orders"),
    ("/ui/asset-types", "Asset Definitions"),
    ("/ui/asset-pool", "Asset"),          # asset_pool.html
    ("/ui/settings", "Settings"),
    ("/ui/audit-log", "Audit"),
]


def test_core_admin_pages_render(logged_in_page: Page) -> None:
    page = logged_in_page
    for path, title_word in _PAGES:
        resp = page.goto(path)
        assert resp is not None and resp.status < 400, f"{path} → {resp.status if resp else 'no response'}"
        expect(page).to_have_title(re.compile(title_word))
