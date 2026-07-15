"""Smoke: the "New Asset Definition" form renders.

Read-only — opens the create form and asserts its heading + the name
field. Deliberately does NOT submit: creating a real asset type would
leave residue in the DB and pull in referential-integrity guards. A
create-and-teardown flow can be added later if the save path needs
coverage.
"""
from __future__ import annotations

from playwright.sync_api import Page, expect


def test_new_asset_type_form_renders(logged_in_page: Page) -> None:
    page = logged_in_page
    page.goto("/ui/asset-types/new")

    # asset_type_form.html h1 == "New Asset Definition" when no asset_type.
    expect(page.get_by_role("heading", name="New Asset Definition")).to_be_visible()
    # The required name input must be present and interactable.
    expect(page.locator('input[name="name"]')).to_be_visible()
