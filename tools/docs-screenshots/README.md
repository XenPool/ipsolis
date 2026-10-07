# Docs screenshots

Regenerates the screenshots in `docs/web/screenshots/` that the `docs/web/*.md`
pages embed — portal pages per locale (DE/EN), admin pages in English only
(the admin UI is not localised), all in light and dark.

```
docker compose up -d                                   # local stack on :8000
.venv-e2e/Scripts/python tools/docs-screenshots/capture.py          # everything
.venv-e2e/Scripts/python tools/docs-screenshots/capture.py admin    # or: portal
```

- Signs session cookies with the API secret — portal as **John Doe**, admin UI
  as the `admin` superadmin. No AD password needed (local dev only).
- `seed.sql` adds demo orders for John and three API tokens (marker
  `docs-screenshot-demo`); `cleanup.sql` removes them again, also when the run fails.
- Before each shot the script hides the update banner and licensee strip and
  replaces IdP tenant/client GUIDs with zeros — check new pages for other
  instance-specific data before committing.
- Output: `portal-<page>-<lang>-<theme>.png` / `admin-<page>-<theme>.png`,
  1920×1200. Docs reference the `-light` file; ipsolis-web renders the `-dark`
  sibling automatically in dark mode. Pages and their docs are listed in
  `PORTAL_PAGES` / `ADMIN_PAGES` in `capture.py`.

## How docs reach ipsolis.com

ipsolis-web fetches `docs/web/**` from the **`prelive`** branch at build time
(`scripts/fetch-docs.mjs`). So: commit on `dev` → merge to `prelive` → rebuild
ipsolis-web (next ipsolis release tag, or run its production workflow manually).
