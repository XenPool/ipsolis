# Changelog

All notable changes to ip·Solis are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per release, entries are grouped under `Added` / `Changed` / `Fixed` /
`Security` / `Migration` headings. The `Migration` section calls out
any DB schema changes; ip·Solis runs Alembic migrations on container
start, so a `docker compose pull && docker compose up -d` is the only
operator step. See [`docs/UPGRADING.md`](docs/UPGRADING.md) (TODO) for
the full upgrade procedure including DB backup recommendations.

## [Unreleased]

### Changed
- **Production overlay renamed:** `docker-compose.prelive.yml` → `docker-compose.prod.yml` to match its actual role (the TLS/nginx production overlay; the old `prelive` name was historical and misled operators). All references updated — CI (`deploy-prelive.yml`), `docker-compose.ghcr.yml` usage comments, `tools/install/bootstrap-certs.sh`, `README.md`, and `docs/DEPLOYMENT.md`/`.de.md` (the `COMPOSE_FILE` examples now use `docker-compose.prod.yml` and the interim "historical name" clarifying comments are removed). The prelive *environment* and its deploy workflow keep their names — only the overlay file was renamed. **Operator action:** hosts that pin the overlay via `COMPOSE_FILE` (e.g. LinPre1/LinPre3) must update the filename; there is no back-compat alias.

## [0.6.12] — 2026-06-24

### Changed
- Aligned and clarified the license wording across the documentation, the bundled `LICENSE` copies, and the Admin UI so they are consistent with the Terms.

## [0.6.11] — 2026-06-23

### Changed
- **Deployment guide is now prebuilt-image (GHCR) only.** `docs/DEPLOYMENT.md` documents a single supported install path — pulling the public `ghcr.io/xenpool/ipsolis-{api,worker}` images — and drops the build-from-source variant throughout (start, update, HA, troubleshooting, clean reset). The image tag now defaults to `:latest`, with per-environment guidance: pin `IPSOLIS_VERSION` in production, track `:latest` in pre-live / test.
- **License module docstring clarified.** `max_users` / `max_asset_types` are parsed for display only and are **not** enforced at runtime (no feature/usage gating — all features ship in every edition). Corrected the misleading "enforces …" wording in `api/app/utils/license.py` and the byte-identical worker copy.

### Fixed
- **License documentation corrected.** The README's License section incorrectly claimed AGPL-3.0 / a "dual-licensed open core". ip·Solis is in fact source-available under the **XenPool Commercial Source License v1.0** (free for non-commercial use + 30-day evaluation; commercial use requires a purchased license). Removed all AGPL references and aligned "open-source" wording to "source-available".
- **Deployment guide:** `sudo chmod +x` for the mkcert binary (it is downloaded as root via `sudo curl`, so the non-sudo `chmod` failed with "Operation not permitted"); replaced the misleading `acme.com` example hostname with `example.com`.

## [0.6.10] — 2026-06-22

### Added
- **Provider-agnostic portal SSO (generic OIDC).** The self-service portal now authenticates users against any standards-compliant OpenID Connect identity provider — Entra ID, Okta, Ping, Google, Keycloak, Authentik, Zitadel, … — through a single code path. Each provider self-configures from its issuer URL via the discovery document (`<issuer>/.well-known/openid-configuration`); adding an IdP is a config entry, not a vendor integration. New helper `api/app/utils/oidc.py` validates ID-token signatures against the provider JWKS (PyJWT) plus iss/aud/exp and the OIDC nonce.
- **OIDC provider registry.** Providers are stored in `app_config` under `idp.<id>.*` (unlimited providers, stable URL-safe ids). Admin → Settings → Authentication gains an add/edit/delete provider UI with a **Test** button that runs a discovery probe. New endpoints: `GET /admin/config/oidc/providers`, `PUT/DELETE /admin/config/oidc/{provider_id}`, `POST /admin/config/oidc/{provider_id}/test`, `PUT /admin/portal-auth`.
- **Login method picker.** When more than one login method is enabled the portal shows a chooser at `/portal/login` (fully localised, 5 locales); with exactly one it redirects straight to it.
- **On-prem LDAP portal login.** Username/password login against on-prem AD/LDAP, offered alongside OIDC via `auth.ldap_enabled` (folds in the earlier short-lived `onprem_ldap` mode).
- **Reuse existing tile logos.** The asset-definition form gains a "Choose existing" logo picker that reuses any logo already attached to another asset type (deduped), no re-upload. New endpoint `GET /admin/asset-type-logos`.
- **Prebuilt-image install (GHCR).** New `docker-compose.ghcr.yml` pulls the public `ghcr.io/xenpool/ipsolis-{api,worker}` images instead of building — faster, version-pinned installs (`IPSOLIS_VERSION`). `locales/` and `scripts/` are now baked into the images (root build context + `.dockerignore`) so a prebuilt install needs no repo checkout of them.

### Changed
- **Parametric OIDC callback.** Portal callback is now `/portal/auth/{provider_id}/callback` (was the Entra-only `/portal/auth/callback`). Logout is generic RP-initiated logout via each provider's `end_session_endpoint`.
- **Portal auth gate.** Replaced the Entra-specific `entra.mode` with `portal.auth_required` (login on/off) + per-provider `idp.<id>.enabled` + `auth.ldap_enabled`. Migration `0003` seeds the toggles and migrates any existing `entra.*` config into `idp.entra.*`. Service-health probe `entra` renamed to `sso` (probes discovery for every enabled provider). SAML 2.0 remains out of scope (separate task).
- **Admin sidebar layout.** Audit Log and API Tokens moved into the compact footer section; theme toggle + signed-in user moved to a sticky top-right header — frees vertical space so the nav no longer needs an internal scrollbar.
- **Script modules are PowerShell-only.** The Script-type dropdown is removed (Python/Bash were never functional — the worker injects a PowerShell-only `$VARS`/`$PARAMS` preamble); the API coerces `script_type` to `powershell`. Module + runbook editors are now English-only.

### Fixed
- **Login-settings save returned 422.** `PUT /admin/config/portal-auth` was shadowed by the generic `/config/{key}` route (parsed as a config key, rejected the body as missing `value`). Moved to `PUT /admin/portal-auth`.
- **Portal i18n on prelive.** Locales are now baked into the api image, so prelive's `volumes: []` ("baked code") no longer leaves `/app/locales` empty (i18n had silently fallen back to English).
- **nginx config + SSL paths.** Mount `nginx.conf` to `conf.d/default.conf` (not the main `nginx.conf`); unified the SSL cert path to `nginx/ssl/` across all compose files and docs.
- **Docs:** corrected all "`alembic upgrade head` is automatic" references (it is not); wrapped long `docker compose` commands; added the first-install alembic step + an SSL pre-flight to the update section.

### Removed
- **Entra-only MSAL login path.** `api/app/utils/entra.py` and the `entra.*` config keys are retired; the generic OIDC path supersedes them (no MSAL-only feature was in use). `msal` is retained only for the legacy Entra credential test.
- **`entra_with_onprem` auth mode.** Removed entirely (never fully implemented; no production deployments).

### CI / Build
- Build images from the **repo-root context** so shared `locales/`+`scripts/` can be baked; added `.dockerignore` to keep secrets (`.env`, `licenses/`, `backups/`, `nginx/ssl`) and `.git` out of the images.
- GHCR packages made **public** (anonymous pull); `actions/checkout` v4 → v5 (Node 20 deprecation).

## [0.6.9] — 2026-06-14

### Added
- **Update Notifier: "Check now" button.** Triggers an immediate update check from the Settings page without waiting for the daily Beat tick. Shows inline feedback: "Up to date", "New version available: vX.Y.Z", or the actual error text — auto-fades after 5 s on success.
- **Update Notifier: GitHub Release created on tag push.** `release.yml` now includes a `create-release` job that extracts the matching CHANGELOG section and publishes a GitHub Release after images are pushed. Previously only a git tag was pushed, causing `releases/latest` to return 404.

### Fixed
- **Update Notifier: 404 no longer shown as error.** When the GitHub repo has no published releases yet, the checker now clears the error field and logs "no releases yet" instead of recording a failure.
- **Update Notifier: error box shows actual error text.** Settings page previously displayed the static "See server logs for details" message regardless of the real error. Now shows the stored `updates.check_error` value.

## [0.6.8] — 2026-06-14

### Added
- **Portal nav badges.** My Approvals, Delegations, and Access Reviews now show a live count badge (red / amber) when the signed-in user has pending items. Loaded via `GET /portal/nav-badges` (3 parallel COUNT queries) on every page without touching existing route handlers.
- **Retry failed orders.** Admin order detail shows an amber "Retry now" button when an order is in `failed` state. Clears the step history, resets the order to `processing`, re-dispatches the runbook, and writes an audit row.

### Fixed
- **Audit log: admin cancel now records the acting user.** `POST /ui/orders/{id}/cancel` was missing `request: Request`, so the audit `triggered_by` showed `celery:dynamic_runner` instead of the admin session. Fixed.

### Changed
- **Admin order detail: Admin Actions moved above Execution Steps.** Cancel / Extend / Retry panel now appears directly below the user and asset cards, before the step log.
- **Docs: ServiceNow webhook fully documented.** `integrations.md` section rewritten from a 4-line stub to a complete integration reference: both auth methods, full payload schema, `curl` example, complete response JSON, capacity checks, idempotency, error table, and audit trail. Fixed incorrect scope name `webhook:servicenow` → `webhook:in`.

## [0.6.7] — 2026-06-13

### Added
- **Portal approval notice.** When an asset type requires manager or application-owner approval, an amber info bar now appears on the order form before the user submits. Message text adapts to the approval combination (manager / owner / both) and switches language live without a page reload.
- **Hard-delete revoked API tokens.** Superadmins can now permanently remove individual revoked token rows via a ✕ button on the API Tokens page. The endpoint (`DELETE /admin/api-tokens/{id}/hard`) requires the token to be revoked first and writes an audit row before deleting.
- **Conditional approver AD validation.** Rule-configured approver emails are now validated against Active Directory at order-creation time (same as manager approval). Orders are blocked with a clear error if a configured approver cannot be resolved as a valid domain account. AD-canonical display name and email are used in all notifications.
- **Docs: Approval Workflow and Conditional Approval Rules.** `docs/web/self-service.md` now contains a full reference for all three approval types, quorum (N-of-M), per-rule quorum groups, approver deduplication, SoD exemption, condition field reference (built-in + `attr.<key>` custom fields), operators, compound logic (ALL/ANY/NOT, max depth 8), and three worked examples.
- **Docs: Parameter system and Parse-from-Script.** `docs/web/automation.md` now documents the `param_schema` structure, what the ↻ "Parse from Script" button does, the PowerShell-to-schema type mapping, and all three parameter scopes (PARAMS / VARS / CTX) with a full CTX key table.

### Fixed
- **Eligible Requestors DN can now be cleared.** Editing an asset definition with the DN field empty no longer silently retains the previous value; the field is unconditionally written (`None` when blank).
- **Retention: FK constraint no longer poisons entire order cleanup batch.** Orders referenced by `asset_pool.current_order_id` are now excluded from the retention DELETE via an `AND id NOT IN (...)` guard, preventing FK RESTRICT violations from aborting the whole batch.
- **Retention: one failing table no longer aborts the others.** Each retention table is now wrapped in its own try/except and commits independently; a failure in one table is logged and reported but does not roll back the others.

### Changed
- **Admin sidebar: three infrequently-used links moved to footer.** Cost Report, Certifications, and Leaver Events are now rendered as small `target="_blank"` links in the sidebar footer, eliminating the scrollbar on standard viewport heights.
- **Admin sidebar footer: Documentation link added.** A book-icon link to `https://www.ipsolis.com/en/docs` appears in the footer alongside Swagger UI.

## [0.6.6] — 2026-06-11
### Changed
- **Legal: AGB and Terms revised (v2).** Sections 1–16 substantively rewritten (free/commercial-use distinction, scope of owed performance, liability structuring). Draft notice removed. Anlage 1 / Annex 1 SBOM table retained.

## [0.6.5] — 2026-06-11

### Changed

- **Legal: SBOM annexes now reference THIRD-PARTY-LICENSES.md.** `AGB.md` Anlage 1 and `Terms-EN.md` Annex 1 intro text updated to declare `THIRD-PARTY-LICENSES.md` as the incorporated-by-reference source for full license texts and copyright notices; version reference updated to v0.6.4.

## [0.6.4] — 2026-06-11

### Changed

- **THIRD-PARTY-LICENSES.md:** replaced link-only table with complete license texts and copyright notices for all 96 Python dependencies, as required by MIT, BSD, Apache 2.0, and LGPL; generated via `pip-licenses --from=mixed --with-license-file`; includes infrastructure (Docker base images) and frontend (HTMX, Tailwind) sections
- **GitHub Actions:** added `run-name` to `release.yml` and `deploy-prelive.yml` so workflow runs display the version tag instead of the commit message

## [0.6.3] — 2026-06-10

### Fixed

- **Admin login page:** removed legacy `ADMIN_API_KEY` hint text and placeholder copy from the login form — the break-glass backend path still works but is no longer advertised publicly
- **AD service account docs:** corrected "read-only" permission note to reflect that write access on group `member` attributes is required for group-based access assignment; added note that additional permissions depend on deployed modules and runbooks

### Changed

- **Deployment docs (EN + DE):** sudo fixes throughout — all writes to root-owned `/opt/ipsolis` now use `sudo`; `cat > file` redirections replaced with `sudo tee`; Option C auto-renewal section clearly marked; nginx config placeholder (`YOUR_HOSTNAME`) replaces hardcoded XenPool test hostname; docs now use `sudo sed -i` to substitute the placeholder
- **Deployment docs:** `docker-compose.nginx.yml` renamed to `docker-compose.prod.yml` and expanded with api/worker production overrides (strip dev volumes, set uvicorn `--workers 4`); all references updated
- **Deployment docs:** section 5 (compose overlay creation) replaced with a note that `docker-compose.prod.yml` is already included in the repository
- **Deployment docs:** docker group prerequisite added to section 1 (`sudo usermod -aG docker $USER`)
- **Deployment docs:** section 7 configuration checklist reordered to match the in-app Setup checklist; added missing items: "Set application title and logo", "Add at least one asset to the pool", Teams approval cards, SIEM, and per-integration API tokens
- **Deployment docs:** beat scaling note moved from section 6 (stack start) to section 12.2 (HA / multi-replica worker) with explanation of `celery-redbeat` distributed lock
- **Deployment docs:** section 7 mentions the included "Virtual Machine Recycler" example runbook as a starting template
- **`nginx/nginx.conf`:** replaced hardcoded `ipsolis-pre.xenpool.local` with `YOUR_HOSTNAME` placeholder; added `client_max_body_size 2g`
- **AGB / Terms:** completed SBOM (Annex 1) with all 35 dependencies across 9 categories, version-pinned with SPDX license identifiers; removed draft blockquote; added English convenience translation (`docs/legal/Terms-EN.md`)
- **TASKS.md:** compressed 3100-line backlog into concise open-tasks + done-summary format; added `[open]` task for `onprem_ldap` portal auth mode

## [0.6.2] — 2026-06-10

### Added

- `docs/DEPLOYMENT.de.md` — full German translation of the production deployment guide; code blocks and commands kept in English per technical documentation convention

## [0.6.1] — 2026-06-10

### Changed

- **CI: docs/changelog updates now triggered by release tag only.** The
  `trigger-docs-rebuild.yml` workflow (prelive-push based) is removed. The
  `release.yml` workflow now dispatches a `changelog-updated` event to
  ipsolis-web after images are pushed, triggering a production rebuild
  directly from the release tag.
- **Docs: PRO_FEATURES.md references removed.** All links and references to
  the deleted `PRO_FEATURES.md` file replaced with inline descriptions in
  `DEPLOYMENT.md` and `README.md`.
- **Docs: Enterprise sizing tier renamed.** Worker sizing table label
  "Enterprise" renamed to "Large" to avoid confusion with the retired
  Enterprise product tier.
- **Legal: AGB added.** Full German terms and conditions for commercial
  ip·Solis licensing added at `docs/legal/AGB.md` (XenPool Commercial
  Source License, dual-track liability model, Munich jurisdiction).

## [0.6.0] — 2026-06-10

### Added

- **License tab: full license text.** Maintenance → License now displays the full
  XenPool Commercial Source License in both English and German, replacing the
  previous "about" link. No external request needed — text is embedded in the template.

### Changed

- **Edition cleanup: all remaining Pro / Community split artifacts removed.** The
  `community` field is stripped from `BeatEntry` and Beat schedule entries; the PRO
  badge on the Beat schedule page is removed. Stale Pro Edition and community-install
  comments removed from `hr_webhook.py`. `enterprise_teaser` import and `nav_locked`
  calls removed from `base.html`. All residual "Pro Edition" / "Community Edition"
  copy removed from `.env.example`, testlab compose files, and docs.

### Fixed

- **Asset type form: section nav and copy.** Section navigation behavior corrected;
  form copy improved for clarity.
- **`base.html` template errors.** Orphaned `enterprise_teaser` import and stale
  `nav_locked` calls removed, resolving template rendering errors on pages that use
  the base layout.

## [0.5.2] — 2026-06-09

### Changed

- **Edition collapse: Pro/Community split removed.** All features are now available in a single public image. Commercial use requires a license under the XenPool Commercial Source License; non-commercial and evaluation use is free. The `PRO_FEATURES.md` doc is removed; the `ipsolis-community` repository is retired.
- **License UI: badge and copy updated.** Dashboard shows "Unlicensed · Free for Non-Commercial Use" when no license is installed. Maintenance → License tab shows "Licensed / Unlicensed / No License" badges instead of "Pro / Community". All "Community Edition" references removed from UI and code.
- **Setup checklist: exclude migration placeholder values.** Fresh installs no longer show false green dots for SMTP (`localhost`), AD (`dc.example.com`), and branding (`Ipsolis`) — the migration seed defaults are now excluded from done-checks.

## [0.5.1] — 2026-06-09

### Changed

- **License: 30-day grace period after expiry.** Pro features remain active for
  30 days after a license expires, covering procurement delays and preventing
  operational outages from missed renewals. After the grace window the instance
  falls back to Community edition automatically. The daily Beat task
  (`license_check`) now fires a warning email each day during the grace period
  and a distinct "now running Community" alert once the grace period ends.
  Admin UI (License page) shows an amber warning banner throughout the window.
- **License: install UUID binding removed.** Licenses are now portable across
  deployments — no `install_uuid` field in the payload, no per-install binding
  check, no reassignment limit. Eliminates the DB read on every Celery worker
  fork (`_register_install_uuid` signal handler), the `set_install_uuid` API
  startup call, and the "Install identity" card on the License admin page.
  Existing signed licenses without `install_uuid` continue to work unchanged;
  existing licenses that contained `install_uuid` are now accepted on any install.

## [0.5.0] — 2026-06-07

### Changed

- **CI: upgrade `actions/github-script` v7 → v8.** Fixes Node.js 20 deprecation on
  GitHub Actions runners (required from June 16 2026). Adds `permissions: {}` block
  and documents that `IPSOLIS_WEB_DISPATCH_PAT` needs `Contents: write` on
  `XenPool/ipsolis-web` for cross-repo dispatch.
- **Terminology sweep — Community / Pro (final pass).** All remaining "Business" /
  "Enterprise" tier references updated across `release.yml`, `main.py`, templates,
  license utilities, `docs/DEPLOYMENT.md`, `docs/onboarding/INSTALL.md`, and
  `docs/web/*.md`. Community mirror strip list corrected: `standalone_runner.py` and
  internal CI workflows (`deploy-prelive.yml`, `trigger-docs-rebuild.yml`) added.
  Community mirror repo URL corrected to `ipsolis-community`.
- **GitHub community health files.** Added `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`,
  `SECURITY.md`, `SUPPORT.md`, issue templates (bug report, feature request), and
  discussion template (Q&A).

## [0.4.13] — 2026-06-07

### Fixed

- **Web docs: Owner Ordering terminology.** `docs/web/self-service.md` section renamed
  from "Deputy Ordering" to "Owner Ordering" to match the v0.4.11 UI rename. Description
  updated to clarify the Owner field identifies the beneficiary of the order, not a
  substitute approver.
- **Web docs: asset status label clarification.** `docs/web/lifecycle.md` notes that the
  admin dashboard "In Use" tile and the asset pool list "Assigned" label both map to the
  underlying `busy` status.

## [0.4.12] — 2026-06-07

### Changed

- **Edition docs aligned to Community / Pro.** `EDITIONS.md` rewritten as a two-tier
  Community / Pro model (the three-tier Community / Business / Enterprise system was
  retired). `README.md` edition matrix updated: vSphere / XenServer moved to Community
  (never Pro-only per the build), standalone runbooks added as a Pro-only row, column
  renamed "Business" → "Pro". `docs/ENTERPRISE_FEATURES.md` renamed to
  `docs/PRO_FEATURES.md`; vSphere / XenServer section corrected from "Pro Edition
  only" to "Community Edition included"; stale "Enterprise license" and
  "Enterprise-gated" notes removed throughout.
- **Code comments and UI copy aligned.** Stale "Enterprise feature" comments in
  `password_policy.py`, `approval_decision.py`, `beat_inventory.py`, `hr_webhook.py`,
  `admin_users.html`, `maintenance.html`, and `settings.html` updated to reflect that
  RBAC (ACL grants, SoD, password policy) and vSphere / XenServer are Community
  features. License expiry email copy updated to "Pro license". `docker-compose.testlab.yml`
  comment updated.

## [0.4.11] — 2026-06-05

### Added

- **Asset Owner concept.** The portal's "Deputy / Stellvertreter" field is renamed to
  **Owner / Besitzende** (all five locales: en/de/es/fr/it) to reflect its real purpose:
  the person an asset is ordered *for* (e.g. an external user who lacks portal access),
  not a substitute approver.
- **Owner access to portal.** Owners set via a MODIFY order now see the asset in
  My IT, My Orders, and the order detail/change/cancel pages — previously only the
  original provision order's `owner_email` was checked; a correlated EXISTS subquery
  now covers ownership established by any completed MODIFY.
- **+ Add buttons** on all user/group fields in the portal modify form (RDP Users,
  Admin Users, Owner) for discoverability alongside the existing Enter-key shortcut.
- **Asset type form hint.** Amber warning below the "Enable RDP/Admin user list"
  checkboxes explains that these only show the portal fields; actual AD group writes
  require matching Group Targets with `principal_source: rdp_users` / `admin_users`.
- **Maintenance deep-link.** `/ui/maintenance#license` now opens directly on the
  License tab via hash-based tab activation.

### Changed

- **Approval gate on MODIFY is add-only.** Re-approval is now triggered only when
  users are *added* to the RDP/Admin lists — removals (privilege de-escalation) go
  straight through without requiring approval. Comparison baseline fixed to use the
  latest completed MODIFY order rather than the original provision order.
- **Delegation self-approval prevention.** When creating re-approval records for a
  MODIFY order, delegation is skipped if the resolved delegate is the same person who
  submitted the change, preventing self-approval loops.
- **Pro edition gating: portal logo.** Custom portal logo settings are now gated
  behind the Pro license; Community installs see a `card_teaser` upgrade prompt.
- **Pro teasers: shop-first flow.** All PRO teasers now link to the
  language-aware ip·Solis Shop (`/de|en|es|fr|it/shop` via `navigator.language`)
  with step-by-step Install ID checkout instructions. `sales@ipsolis.com` removed.
- **Maintenance License tab "How it works"** updated: copy Install ID → visit shop →
  upload `.lic`. `sales@ipsolis.com` removed.

### Fixed

- **Owner field persistence.** My IT detail page now reads `owner_email` /
  `owner_name` from the latest completed MODIFY order, so the set owner is correctly
  displayed after page reload (previously the provision order's empty value was shown).
- **Approval routing for owner-submitted changes.** When the owner (not the requester)
  submits a MODIFY and the configured application-owner approver has a delegation
  active pointing back to the submitter, delegation is now skipped so the approval
  stays with the original approver.

### Removed

- **Orphaned `license.html` template** (no serving route existed) deleted.

## [0.4.10] — 2026-06-02

### Changed

- **Migration squash.** All 96 incremental Alembic migrations have been consolidated
  into a single `0001_initial_schema.py`. Fresh installs are unaffected. Existing
  instances (dev / prelive) require a one-time version stamp:
  `UPDATE alembic_version SET version_num = '0001';`
- **README.** Quick Start section renamed from "Development" to "Local".
- **ORM model registry.** Seven models previously missing from `models/__init__.py`
  (`ScriptModule`, `RunbookDefinition`, `RunbookStep`, `PsModule`, `GlobalVar`,
  `DbBackup`, `OrderChangeLog`) are now registered, fixing FK resolution in
  `alembic autogenerate`.

## [0.4.9] — 2026-06-01

### Fixed

- **Order creation 500 error.** Placing an order via the portal, the `/orders` API, or
  the ServiceNow webhook raised `AttributeError: AssignmentModel has no attribute
  'DEDICATED_SHARED'` because that enum value was removed in a prior refactor but the
  per-user quota guard in `portal.py`, `orders.py`, and `webhook.py` still referenced it.
  The dead conditional has been removed; `enforce_max_per_user` is now always applied.
- **License key rotation (commercial-2026).** Updated Ed25519 signing key used to verify
  Pro license files.

### Changed

- **Maintenance › License tab.** Added `showBanner()` helper for consistent inline
  feedback messages on the license status panel.

## [0.4.8] — 2026-05-11

### Changed

- **Update Notifier simplified.** The configurable "Repository (GitHub API)"
  and "GitHub token" fields have been removed from Settings. The Community
  edition repo is now public on GitHub, so no token is required and the
  endpoint is hardcoded. Only the enabled / disabled toggle remains.

## [0.4.7] — 2026-05-11

### Added

- **Community example scripts (migration 0096).** Three ready-to-use PowerShell
  script modules — `Example - Provision Asset`, `Example - Change Asset`, and
  `Example - Deprovision Asset` — are now seeded on every fresh install. They
  demonstrate the standard module pattern (param block, `$VARS` access, JSON
  output, try/catch) for use in asset-type runbooks.

### Changed

- **Standalone Runbooks are now PRO-only.** The sidebar nav item shows a locked
  PRO badge on Community installs; navigating to `/ui/standalone-runbooks`
  renders an upgrade teaser instead of the runbook list. The API route
  (`admin_standalone_runbooks.py`) and the Celery worker task
  (`standalone_runner.py`) are stripped from Community images at build time,
  and the Beat cron-schedule entry is omitted when the task is absent.
- **Script module seed data split by edition.** The full set of production
  script modules (AD, SCCM, XenServer/XCP-ng, VMware, SQL) is PRO-only seed
  material. The community mirror now ships only `scripts/modules/examples/`;
  the `scripts/runbooks/` directory (Virtual Machine Recycler etc.) is also
  excluded from Community builds.

## [0.4.6] — 2026-05-10

### Fixed

- **PRO feature gating in Community edition.** Community installs now
  show locked PRO badges (violet) on all PRO-only nav items and settings
  sections instead of either hiding them completely or granting full access.
  Affected surfaces: Certifications, Leaver Events (sidebar nav), SIEM
  (Settings → Compliance), SCCM (Settings → SCCM tab), and
  vSphere / XenServer (Settings → Hosting Infra tab).
- **Settings page layout broken for E-Mail, Compliance, SCCM, and
  Hosting Infra tabs.** A missing `<div id="edit-modal">` outer wrapper
  in the Script Variables tab caused a stray `</div>` to close the main
  content container early, pushing every subsequent tab panel outside
  the page layout.
- **Dashboard banner incorrectly showed "PRO Edition" on Community
  installs.** The edition check now requires `edition == 'pro'`; Community
  installs show no banner.
- **Certifications and Leaver Events appeared as active links in Community
  when running a non-stripped image** (e.g. dev). Nav logic now gates
  on `edition` rather than `has_certifications` / `has_leaver_events`.
- **SCCM health probe called a stripped worker task on Community.**
  `_probe_sccm` now short-circuits with `{"ok": null, "detail": "PRO feature"}`
  when `edition != "pro"`, matching the N/A display of unconfigured services.

### Changed

- `enterprise_teaser.html` unified to a single **PRO** tier (previously
  had separate ENT / BUS tiers with amber / blue distinction). All gated
  features now show a consistent violet PRO badge. The partial is no
  longer stripped from the Community Docker image so teasers render
  correctly without the PRO code present.
- Community mirror workflow updated to retain `enterprise_teaser.html`
  in the public source tree (required for teaser rendering in community
  builds from source).

## [0.4.5]

  Range:  v0.4.4..HEAD
  Date:   2026-05-09

### Added

- add installation guide and environment variable setup (`3dc57ff`)

### Documentation

- remove completed compose-rename migration runbook (`1ed6c96`)

### Other

- remove orphaned and obsolete files (`738964f`)

## [0.4.3] — 2026-04-28

### Added

- add option to create AD groups if missing during grant (`420429d`)

## [0.4.2] — 2026-04-28

### Added

- Implement update notifier and password policy features (`30afc66`)
- add PowerShell and bash scripts for release management (`ecd252b`)
- refresh license globals on config refresh (`6d600fe`)

### Changed

- update project name to 'ip·Solis' across documentation and code (`0fa6328`)

## [0.4.1] — 2026-04-27

### Added

- **RBAC slice 4 — password rotation, lockout, SoD per-rule opt-out,
  token mint guard relaxation.** Operators can now configure forced
  password rotation (`rbac.password_rotation_days`, 0 disables) and
  lockout-on-N-failed-attempts (`rbac.lockout_threshold`,
  `rbac.lockout_duration_minutes`). Failed-login attempts are tracked
  per admin user; lockouts auto-expire after the configured window.
  Approval rules accept `sod_exempt: true` so a static compliance
  officer who is also an admin can sign off on orders for asset types
  they configured. `/admin/api-tokens` router gate relaxed from
  `superadmin` to `admin`; the existing mint guard prevents privilege
  escalation. `/admin/maintenance/*` GET endpoints now reachable by
  `auditor` for compliance review; writes still require `admin`.
- **RBAC (Community).** Per-asset-type ACL grants, role-bound
  API tokens, SoD enforcement, and password policy ship in all editions.
  Community installs include the full role ladder, per-user accounts,
  scoped grants, role-bound tokens, enforced SoD, and password policy.
- **Testlab compose stack** (`docker-compose.testlab.yml`) bundling
  Vault dev mode, rsyslog, and a mock SIEM/webhook receiver so SIEM
  / secret-backend / webhook integrations can be smoke-tested
  without paying the resource bill of full Splunk / Sentinel /
  CyberArk lab installs. Splunk Free is profile-gated (heavy image)
  and brought up via `--profile splunk` on demand.
- **Role-aware Admin UI navigation.** Each nav item is gated by the
  signed-in admin's role so a helpdesk user lands on a clean
  6-item nav instead of seeing every page and 403'ing on every
  click. Asset-type form shows a read-only banner with disabled
  Save button when the role can't write.
- **Runbook step editor — categorised module dropdown.** Modules
  are grouped server-side by their `"CATEGORY - Name"` prefix
  (`<optgroup>`); description, category badge, parameter list, and
  an "Edit module ↗" deeplink render in a card below the dropdown
  instead of being squeezed into the option text.
- Implement update notifier and password policy features (`30afc66`)

### Changed

- **License changes refresh template globals immediately.** Uploading
  or removing a license now refreshes the `is_enterprise` / `edition`
  / `license_info` Jinja env globals as part of the same request, so
  the Dashboard and feature-gated nav blocks reflect the new edition
  without an api restart.
- **Approval-delegations nav link gated to `admin`+** to match the
  API gate (the page is "admin manages delegations on behalf of
  users", not self-service).
- **Asset-type form result banner moved above the sticky save bar**
  so 4xx responses (validation, role mismatch) are visible without
  scrolling past the bar pinned to the viewport bottom.

### Fixed

- **Vault testlab healthcheck** — the alpine Vault image has no
  `wget`; switched the healthcheck to `vault status`.

### Migration

- `0073_rbac_slice4` adds three columns to `admin_users`
  (`password_set_at`, `failed_login_count`, `locked_at`), one column
  to `order_approvals` (`sod_exempt`), and seeds three policy keys
  (`rbac.password_rotation_days`, `rbac.lockout_threshold`,
  `rbac.lockout_duration_minutes`). All defaults disable enforcement
  so existing installs see no behaviour change.
- `0074_update_check_config` (this release) seeds the update-checker
  feature config keys (`updates.check_enabled`, `updates.repo_url`,
  plus four cursor keys the daily Beat task fills in). All disabled
  by default.

## [0.4.0] — 2026-04-26

First internal release snapshot. No public deployments yet — versioned
in the 0.x range until the API surface is committed (1.0 marks the
"safe to integrate against" boundary). Highlights:

- Self-service portal + admin UI + ServiceNow webhook
- Asset type, runbook, and standalone-runbook orchestration
- VDI and Server lifecycle workflows on XenServer/XCP-ng + vSphere
- SCCM task-sequence integration (NTLM today; Kerberos backlogged)
- Active Directory user/manager/group lookups via msldap
- Entra ID portal SSO via MSAL
- Conditional approval rules with N-of-M quorum support
- Approval delegations + reminder/escalation Beat tasks
- SIEM streaming (Splunk HEC, Microsoft Sentinel, generic webhook)
- External secret management (Vault KV v2, CyberArk CCP)
- OpenTelemetry tracing, Audit log retention by classification
- HA Beat (RedBeat distributed lock)
- RBAC slices 1-3: 5-tier role ladder, per-user accounts,
  per-asset-type ACL grants, role-bound API tokens, SoD enforcement
