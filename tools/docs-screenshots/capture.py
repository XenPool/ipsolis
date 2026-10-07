"""Capture the screenshots embedded in docs/web/*.md (portal + admin UI).

Runs against the local dev stack (http://localhost:8000): seeds demo data
(seed.sql), captures every page in each theme — portal pages also per locale —
then removes the demo data again (cleanup.sql), also on failure.

Login: instead of an LDAP bind against AD / an admin password, the script signs
session cookies with the API's own secret (read inside the api container) —
portal as John Doe, admin UI as the `admin` superadmin. Local dev only; it
needs `docker exec` access to the running stack.

Usage (from the repo root, with the e2e venv from tests/e2e/README.md):
    .venv-e2e/Scripts/python tools/docs-screenshots/capture.py [portal|admin]

Output (docs/web/screenshots/, 1920x1200):
    portal-<page>-<lang>-<theme>.png   — the portal is localised (DE/EN)
    admin-<page>-<theme>.png           — the admin UI is English-only
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "docs" / "web" / "screenshots"
BASE = os.environ.get("IPSOLIS_BASE_URL", "http://localhost:8000").rstrip("/")

THEMES = ("light", "dark")
# 1440x900 CSS px at 4/3 scale → 1920x1200 PNGs, same as the marketing captures.
VIEWPORT = {"width": 1440, "height": 900}
SCALE = 4 / 3

PSQL = ["docker", "exec", "-i", "ipsolis-postgres", "sh", "-c",
        'psql -q -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-ipsolis}"']

PORTAL_SESSION = {"portal_user": {"email": "john@xenpool.de", "name": "John Doe", "oid": "john",
                                  "upn": "john@xenpool.local", "provider": "ldap"}}
ADMIN_SESSION = {"admin_authenticated": True, "admin_user": "admin",
                 "admin_role": "superadmin", "admin_via": "user"}

SIGN_PY = """
import json, base64, sys
from itsdangerous import TimestampSigner
from app.config import settings
print(TimestampSigner(str(settings.API_SECRET_KEY)).sign(base64.b64encode(sys.argv[1].encode())).decode())
"""

# Instance-specific details that have no place in public product docs: the
# "vX is available" update banner, the licensee strip on the dashboard, and
# the IdP tenant/client GUIDs of this dev instance.
HIDE_INSTANCE_CHROME = """
() => {
  document.querySelectorAll('span').forEach(s => {
    if (/is available/.test(s.textContent)) s.closest('.px-6')?.remove();
  });
  document.querySelectorAll('strong').forEach(s => {
    if (['Licensed', 'Evaluation license'].includes(s.textContent.trim())) s.parentElement.remove();
  });
  // Tenant / client IDs of the real IdP config (settings page) → placeholder.
  const GUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
  document.querySelectorAll('input').forEach(i => {
    const v = i.value.replace(GUID, '00000000-0000-0000-0000-000000000000');
    if (v !== i.value) i.value = v;
  });
}
"""


def run_sql(name: str) -> None:
    sql = (HERE / name).read_text(encoding="utf-8")
    subprocess.run(PSQL, input=sql, text=True, check=True)


def sign(session: dict) -> str:
    import json
    return subprocess.run(
        ["docker", "exec", "ipsolis-api", "python", "-c", SIGN_PY, json.dumps(session)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def click_text(label: str):
    def act(page) -> None:
        page.get_by_text(label, exact=True).first.click()
        page.wait_for_timeout(400)
    return act


# (file stem, path, optional action before the shot)
PORTAL_PAGES = [
    ("home", "/portal/", None),
    ("catalog", "/portal/orders/new", None),
    ("order-new", "/portal/orders/new", click_text("Developer Workstation")),
    ("my-it", "/portal/my-it", None),
    ("orders", "/portal/orders", None),
]

ADMIN_PAGES = [
    ("pool", "/ui/asset-pool", None),                               # lifecycle
    ("asset-types", "/ui/asset-types", None),
    ("asset-type", "/ui/asset-types/28/edit", None),
    ("operations", "/ui/operations", None),
    ("certifications", "/ui/certifications", None),
    ("runbook-editor", "/ui/runbooks/18/edit", None),               # automation
    ("modules", "/ui/modules", None),
    ("ps-modules", "/ui/ps-modules", None),
    ("standalone-runbook", "/ui/standalone-runbooks/2/edit", None),
    ("audit-log", "/ui/audit-log", None),                           # compliance
    ("cost-report", "/ui/cost-report", None),                       # finops
    ("integrations-settings", "/ui/settings", click_text("Authentication")),  # integrations
    ("api-tokens", "/ui/api-tokens", None),
    ("rbac-users", "/ui/admin-users", None),                        # security
]


def shoot(browser, cookie: str, pages, theme: str, lang: str | None, login_marker: str) -> None:
    ctx = browser.new_context(
        viewport=VIEWPORT, device_scale_factor=SCALE, color_scheme=theme,
        locale="de-DE" if lang == "de" else "en-GB",
    )
    init = f"localStorage.setItem('theme', '{theme}');"
    if lang:
        init += f"localStorage.setItem('portal_lang', '{lang}');"
    ctx.add_init_script(init)
    ctx.add_cookies([{"name": "xp_session", "value": cookie, "url": BASE}])
    page = ctx.new_page()
    for stem, path, action in pages:
        page.goto(BASE + path, wait_until="networkidle")
        if login_marker in page.url:
            sys.exit(f"Not logged in — redirected to {page.url}")
        if lang:
            # i18n.js swaps strings async; HTMX fragments load after first paint.
            page.wait_for_function("!document.documentElement.classList.contains('i18n-pending')")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        if action:
            action(page)
        page.evaluate(HIDE_INSTANCE_CHROME)
        prefix = "portal" if lang else "admin"
        name = f"{prefix}-{stem}-{lang}-{theme}.png" if lang else f"{prefix}-{stem}-{theme}.png"
        page.screenshot(path=str(OUT / name))
        print(f"  ✓ {name}")
    ctx.close()


def capture(which: set[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in THEMES:
            if "portal" in which:
                for lang in ("de", "en"):
                    shoot(browser, sign(PORTAL_SESSION), PORTAL_PAGES, theme, lang, "/portal/login")
            if "admin" in which:
                shoot(browser, sign(ADMIN_SESSION), ADMIN_PAGES, theme, None, "/ui/login")
        browser.close()


def main() -> None:
    which = set(sys.argv[1:]) or {"portal", "admin"}
    run_sql("cleanup.sql")  # leftovers from an aborted run
    run_sql("seed.sql")
    try:
        capture(which)
    finally:
        run_sql("cleanup.sql")
        print("Demo data removed.")


if __name__ == "__main__":
    main()
