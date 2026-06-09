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
