# ip·Solis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.

---

## Open Tasks

> Tasks below down to _Portal accessibility_ were added on 2026-07-14 from the codebase audit
> ([`AUDIT-FINDINGS.md`](AUDIT-FINDINGS.md)). Rough priority order, highest first.

### [open] Order groups — header/line-item model with header-level approval

Today one order = one asset = one approval run: [`Order`](api/app/models/order.py) is a flat
row (`asset_type_id`, `assigned_asset_id`, `config`, `provisioned_state`, `celery_task_id`,
`requested_from/until`, `OrderStep` children) and [`OrderApproval`](api/app/models/approval.py)
rows hang directly off it. This blocks any multi-item request (onboarding bundles, later a
shopping cart). Rather than turn `Order` into a header — which would force touching every
`orders` consumer ([`AssetPool.current_order_id`](api/app/models/asset.py), the cost report,
certification campaigns, the leaver flow, the ServiceNow webhook, and the SCIM endpoint's
`DISTINCT orders.user_email` derivation in [`scim.py`](api/app/routes/scim.py)) — invert the
model: add a new header entity **`OrderGroup`** on top, with 1..n existing `Order` rows as line
items. A classic single order becomes the special case "group with exactly one item", and all
existing FK consumers plus the per-order execution/lifecycle machinery stay untouched.

**Scope:**
- New entity `OrderGroup` (table `order_groups`): requester (email/name), recipient/owner,
  derived status, timestamps, and an `origin` enum covering all real sources —
  `portal`, `servicenow`, `api`, `rule_based`. [`Order`](api/app/models/order.py) gets an
  `order_group_id` FK (backfilled, then NOT NULL).
- Approval moves to the header but stays **item-scoped**: [`OrderApproval`](api/app/models/approval.py)
  rows attach to the `OrderGroup` and each row records which item(s) it covers. Approval
  requirements are still computed per item from its [`AssetType`](api/app/models/asset.py)
  (`requires_manager_approval` / `requires_owner_approval` / `approval_owners` / `approval_rules`
  / classification routing / `min_approvals_required` N-of-M / SoD — semantics unchanged). An item
  counts as approved when its own requirements are met; the approver sees **one** approval task
  per group.
- The approver can reject individual items with a **mandatory per-item rejection reason**;
  remaining items proceed. Items keep the existing [`OrderStatus`](api/app/models/order.py)
  lifecycle (`pending_approval`, `scheduled`, `provisioning`, `provisioned`, `failed`, `revoking`,
  `revoked`, `expired`, `cancelled`, `rejected`) — **no new item-status enum**.
- Group status is **derived** from item statuses (e.g. `pending_approval`, `partially_approved`,
  `in_progress`, `completed`, `rejected`). Decide and document whether it is computed on read or
  persisted as a cache.
- Execution stays per item: the worker, `OrderStep` tracking, provisioning, revoke, expiry and
  reminders are untouched.
- **No quantity field** — one `Order` row per unit/instance, which keeps `max_per_user` checks and
  pool-reservation logic intact.
- Alembic migration that backfills one `OrderGroup` per existing order and re-links its
  `OrderApproval` rows to the group. Document a rollback consideration (dropping the header while
  line items survive).
- Self-service UI stays functionally unchanged here (it creates groups with exactly one item;
  no cart UI). The approval UI **and** the external e-mail approval flow
  ([`approvals_external.py`](api/app/routes/approvals_external.py)) must both support per-item
  decisions.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** cart UI (future task; conceptually an `OrderGroup` in "draft" status);
bundles and assignment rules (see the entry below); any SCIM endpoint changes.

---

### [open] Onboarding bundles and attribute-based assignment rules

Pre-work for the open [SCIM provisioning (joiner/mover/leaver → asset lifecycle)](#open-scim-provisioning-joinermoverleaver--asset-lifecycle)
task — bundles are the target that SCIM joiner/mover events will trigger — but they must also work
standalone without SCIM (manual order in self-service, admin-triggered evaluation). **Depends on
the [Order groups](#open-order-groups--headerline-item-model-with-header-level-approval) entry
above.** Bundles define **no new assets**; they only reference existing
[`AssetType`](api/app/models/asset.py) rows, which remain the single source of truth. Because
ip·Solis has no local user store (portal users are session-only via OIDC/LDAP; requester
attributes are resolved from AD at order-creation time and frozen on the order), rule evaluation
is an **internal service that takes a user-attribute dictionary as input** — not a hook on a
(nonexistent) user entity.

**Scope:**
- New entity `Bundle`: name, description, active flag, ordered list of positions. Each position
  references an `AssetType`, with a required/optional flag and an optional default attribute
  selection (pre-fill for [`Order.config`](api/app/models/order.py), drawn from
  `AssetType.config`). No quantity — one item per unit, consistent with the order-groups entry.
- New entity `AssignmentRule`: condition on user attributes (department, cost center, group
  membership, …) → bundle. **Reuse** the existing conditional-approval-rule condition format and
  its AND/OR/NOT visual editor pattern (`AssetType.approval_rules`) instead of inventing a new
  rule syntax.
- Rule-evaluation service with explicit trigger points:
  (a) manual admin action "evaluate onboarding rules for user X" (attributes resolved from AD via
  the existing `ad.attribute.*` mechanism, with manual override);
  (b) optionally on first portal login (config flag, default off);
  (c) SCIM create/update — wired up later in the separate SCIM task, **out of scope here**.
  Define an idempotency/dedup rule: never order an asset type the user already has an **active**
  (non-revoked, non-expired) order for.
- A bundle trigger creates **one** `OrderGroup` with N line items through the existing
  order/approval/execution paths. No bundle-specific approval logic — the approver can strike
  individual (e.g. optional) items and the rest proceeds, exactly the mechanism from the
  order-groups entry.
- Line items **freeze** the bundle position at order time (snapshot of bundle id/name and resolved
  position config) for auditability; the bundle itself references `AssetType` rows live. Mirrors
  the existing freeze pattern (`OrderApproval.rule_name` / `rule_threshold`,
  `Order.provisioned_state`).
- Bundles are additionally orderable in the self-service catalog ("order package") and produce the
  **same** `OrderGroup` shape as the rule-based trigger.
- Admin UI for bundles and assignment rules.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** the SCIM endpoint itself (existing separate task); mover/leaver workflows
(follow-up task); cart UI.

---

### [open] SCIM provisioning (joiner/mover/leaver → asset lifecycle)

Extend SCIM 2.0 beyond the current **leaver-focused** subset (already shipped) to full
**joiner/mover/leaver** provisioning that drives the asset lifecycle — a drop-in target for
Okta / SailPoint / Ping provisioning workflows. Higher strategic value than SAML. Split out of
the provider-agnostic SSO task on 2026-06-24.

**Scope / related:**
- Builds on the existing `/scim/v2/*` endpoint (ServiceProviderConfig, ResourceTypes, Schemas,
  Users CRUD; DELETE / PATCH `active=false` already trigger the leaver flow).
- Pulls in the deferred "HR feed + SCIM slice 2": full SCIM filter grammar, `/Groups` shim,
  bulk operations.
- Joiner/mover → asset lifecycle mapping is the new design work (how SCIM create/update maps to
  asset orders / access grants).
- The joiner/mover → asset-lifecycle mapping builds on the two new tasks
  ([Order groups](#open-order-groups--headerline-item-model-with-header-level-approval) and
  [Onboarding bundles + assignment rules](#open-onboarding-bundles-and-attribute-based-assignment-rules)):
  a SCIM **joiner** event evaluates assignment rules and creates a bundle `OrderGroup`, so SCIM
  create/update must become a real trigger (today `POST /scim/v2/Users` is a no-op —
  [`scim.py`](api/app/routes/scim.py)). That in turn requires persisting a minimal user/identity
  projection so attribute changes (**mover**) can be diffed against the last-seen state — ip·Solis
  has no local portal-user store today, so joiner/mover diffing needs one.
- **Mover reconciliation (explicit deliverable — sharpened 2026-07-14 audit):** on a diffed
  attribute change, re-run the assignment-rule service against the new attribute set, diff the
  resulting target bundle/asset set against the user's active orders, and reconcile — create orders
  for newly-entitled asset types (through the normal approval path) and revoke orders for lost
  entitlements. Reuses the onboarding-bundle rule engine and the existing revoke flow; the mover is
  a *delta* over the joiner, not new machinery. Depends on the Onboarding bundles task.

---

### [open] Access Targets — only `ad_group` is implemented
Of the four **Access Target** types offered in the asset-type form, only **AD Group**
(`ad_group`) works end-to-end. The other three are now shown as **"(coming soon)"** and
**disabled** in the UI ([`asset_type_form.html`](api/app/templates/ui/asset_type_form.html),
all three dropdown render paths) so operators can no longer save targets that fail silently
at provision time. Current backend state in
[`target_executor.py`](worker/tasks/modules/target_executor.py):

- **`entra_group`** — stub: `_grant_entra_group` / `_revoke_entra_group` raise
  `NotImplementedError` ([`target_executor.py:237-239`](worker/tasks/modules/target_executor.py) / `:271-273`).
  To finish: implement grant/revoke via **Microsoft Graph**
  (Application Permission `GroupMember.ReadWrite.All`) for Entra cloud-only security groups
  on asset types defining `{"type": "entra_group", "identifier": "<group-id>"}`. Fits the
  existing Entra/OIDC stack. **Priority: raised (2026-07-14 audit)** — this is the sell against
  cloud SaaS for M365-/cloud-only customers: the gap between "Entra login" (OIDC shipped) and
  *provisioning* Entra groups. The dispatch-table + change-log + idempotency framework
  (`grant()` / `revoke()`) is target-agnostic — only the two stub handlers need filling, no other
  plumbing change. Re-enable the UI guard for `entra_group` once the handlers land.
- **`rds_collection`** / **`other`** — no handler at all (fall into the "Unknown target type"
  branch). These are **not** planned as native target types — RDS session-collection
  membership etc. belongs in a **custom runbook step** (PowerShell `Add-RDUserToSessionCollection`).
  Re-enable in the UI only if/when a real native handler is added.

**Follow-up:** add server-side validation of `target_type` (reject anything but `ad_group`
until handlers exist) so the disabled-option UI guard is backed by the API.

---

## Done — Summary

All items below are shipped. Detailed implementation notes live in git history.

| Area | Shipped | Notes |
|------|---------|-------|
| Portal accessibility — structural (BITV/EN 301 549 basics) | 2026-07-14 | Structural-first a11y at the shared portal choke points, **no visual redesign** (layout/colors/components unchanged). [`base_portal.html`](api/app/templates/portal/base_portal.html): skip link (visually hidden until keyboard focus) → new `<main id="main-content" tabindex="-1">` target; landmark labels on `<aside>` / `<nav>` (translated via `data-i18n-attr-aria-label`); decorative nav SVGs `aria-hidden` + `focusable=false`; notification badges `role="status" aria-live="polite"` (screen-reader announces count changes); an early inline `lang`-set from `localStorage.portal_lang` before first paint (closes the hardcoded-`en` gap ahead of i18n.js's own `apply()` which already set `<html lang>`); scoped `:focus-visible` outline (keyboard focus only — invisible to mouse users) + skip-link CSS, **portal-scoped so admin UI is untouched**. [`_partials/language_switcher.html`](api/app/templates/_partials/language_switcher.html): `aria-expanded` toggle synced on open/select/click-away, `aria-controls`, `role=menuitem` on options, decorative flag/chevron SVGs `aria-hidden`. New a11y strings (`portal.a11y.skip_to_content` / `.sidebar`, `portal.nav.aria_label`) in **all 5 locales** — validator green at 221 keys parity. **Verified**: templates compile + rendered base_portal contains every marker (skip link, main target, landmarks, aria-hidden, live badges, early-lang, focus CSS, switcher aria-expanded). **Scope**: portal only (admin excluded); **not a conformance claim** — closes structural basics. **Out of scope / follow-up**: formal Barrierefreiheitserklärung + external BITV test, full WCAG 2.1 AA, contrast audit, login-page layout (separate template) |
| Signed attestation artifacts (handover + revocation) | 2026-07-14 | Two ISO-27001 evidence artifacts on one signed-token HTML mechanism (no PDF). New `AttestationArtifact` ([model](api/app/models/attestation_artifact.py), table `attestation_artifacts`, migration [`0007`](api/alembic/versions/0007_attestation_artifacts.py)) + per-type flags `requires_handover_ack` / `emit_revocation_certificate` (default off, on the asset-type form). **Emission** ([`attestation.py`](worker/tasks/modules/attestation.py)) is hooked at the three order-completion points in [`dynamic_runner.py`](worker/tasks/workflows/dynamic_runner.py) via one idempotent best-effort helper: **handover** (Übergabeprotokoll) on `provisioned` (status `pending`, emails a signed ack link) and **revocation/disposal certificate** on `revoked` (status `emitted`, cites the just-rolled-back `order_change_log` grants; expiry-driven revokes flow through the same delete-order completion). Signed token ([`attestation_token.py`](api/app/utils/attestation_token.py) + worker mirror, `kind=attestation`, 90-day TTL) reuses the certification-link HMAC pattern. **External pages** (no auth): `GET/POST /attestation/{token}` ([`attestation_external.py`](api/app/routes/attestation_external.py)) — handover ack page (records `acknowledged_by`/`_at`, audited) + printable revocation cert. **Admin**: read API [`admin_attestations.py`](api/app/routes/admin_attestations.py) + a **Reports → Attestations** page (filter by kind/status, per-row signed viewer link, count tiles); Settings → Attestations card (AUP text + reminder config). **Overdue-ack reminder** Beat task ([`attestation_reminders.py`](worker/tasks/workflows/attestation_reminders.py), daily 08:30, opt-in, deduped via `last_reminder_at`). **Verified end-to-end on the compose stack:** emit both kinds for a real provisioned order (idempotent — no dup), handover page renders + ack flips `pending→acknowledged`, revocation cert renders w/ print, bad token → 410, admin list + counts, reminder fires-then-dedups, full audit trail (emitted/acknowledged/reminder_sent), all templates compile. **i18n N/A** (admin UI + worker artifacts English, like Teams cards). **Out of scope:** server-generated PDF, eIDAS e-signatures, QR physical inventory |
| Slack approval delivery (delta) | 2026-07-14 | Slack as a second, independent delivery channel alongside the existing Teams path (Teams untouched). New [`slack_notify.py`](worker/tasks/modules/slack_notify.py) (worker) + [`slack_notify.py`](api/app/utils/slack_notify.py) (api mirror, cross-image duplicate like Teams): `post_message` (Slack incoming webhook) + `build_approval_message` (Block Kit — header / greeting / fact section / "Review request" URL button + notification-fallback `text`). Wired as a best-effort branch into [`deliver_approval_notification`](worker/tasks/workflows/dynamic_runner.py) (so it covers **both** initial dispatch and the reminder Beat task, with the "Reminder (n)" headline bump), reading `slack.mode` / `slack.webhook_url` in `send_approval_requests` + `approval_reminders`. **Reuses the channel-agnostic signed approval token** (`make_approval_token`), so the one-click `/approve/{token}` URL is identical across email/Teams/Slack. `POST /admin/config/slack/test` ([`admin.py`](api/app/routes/admin.py)) mirrors the Teams test; Settings → Slack section + save/test JS ([`settings.html`](api/app/templates/ui/settings.html)); setup-checklist item ([`admin_setup.py`](api/app/routes/admin_setup.py)); config seeded by migration [`0006`](api/alembic/versions/0006_slack_approval_config.py) (`slack.mode`, `slack.webhook_url` is_secret). **Verified on the compose stack:** `post_message` HTTP mechanics (200 → success, 400 → error-with-detail, empty → config error), test endpoint states (disabled → ok:null, enabled+bad-webhook → ok:false), config round-trip (webhook stored as secret), Block Kit shape, all templates compile. Real-Slack visual render is the operator's final manual check (no live Slack webhook in the lab). **i18n N/A** (admin UI + worker-generated message English, same as Teams cards). **Out of scope:** interactive approve-in-Slack buttons (links out to the signed URL, like Teams) |
| Software license & contract lifecycle | 2026-07-14 | New **`SoftwareContract`** entity ([`software_contract.py`](api/app/models/software_contract.py), table `software_contracts`, migration [`0005`](api/alembic/versions/0005_software_contracts.py)) — the *customer's* vendor contracts (Adobe, M365, …), deliberately named apart from the product `.lic` gating in [`admin_license.py`](api/app/routes/admin_license.py). Fields: vendor, product, contract_value, currency, billing_interval (monthly/quarterly/annual), licensed_seats (null=unlimited), start/renewal_date, notice_period_days, auto_renew, cost_center, notes. **Binding: 1 contract : N asset types** via a new `asset_types.contract_id` FK (0..1, `ON DELETE SET NULL`); License/Contract dropdown on the [asset-type form](api/app/templates/ui/asset_type_form.html) (create/update honour explicit-null unbind via `model_fields_set`; clone + `_type_snap` carried). **CRUD** in [`admin_contracts.py`](api/app/routes/admin_contracts.py) (`/admin/contracts`, auditor-read / admin-write) with live seat consumption per row. **Cost integration — Model A** (actual consumption × per-seat price; unused seats = *shelfware*, unrecovered): [`admin_cost_report.py`](api/app/routes/admin_cost_report.py) `_query_active_orders` now LEFT JOINs the contract and prices each active order at `contract_monthly_value ÷ seats` (falling back to the type's own `monthly_cost` when unbound/unlimited), so the existing provider/consumer chargeback + CSV automatically reflect real contract cost; plus a new live `by_contract` view (seat price, consumption, allocated, shelfware, utilization, over-allocation, renewal aging) surfaced on both the [Licenses & Contracts](api/app/templates/ui/contracts.html) admin page (new Reports-hub tab) and a section on the cost-report page. **Renewal reminders:** daily opt-in Beat task [`contract_renewals.py`](worker/tasks/workflows/contract_renewals.py) (08:15) emails when a contract enters its `renewal_date − notice_period_days` window, deduped via `last_renewal_reminder_at`, audited (→ SIEM); config `contract.renewal_reminder_enabled` / `_email` (falls back to `health.alert_email`); registered in `include`/`beat_schedule`/`beat_inventory`. **Verified end-to-end on the compose stack:** seat-price math (72000 EUR/yr ÷ 100 = 60/seat), bind 2 types → consumption 6 → allocated 360 / shelfware 5640, orders re-priced at the seat rate through the whole report, over-allocation flag (seats<used), renewal reminder fires-once-then-dedups + audits, FK SET-NULL unbind on delete, validation (bad interval 422), all templates compile. **Migration export:** `contract_id` deliberately **not** exported (cross-row FK id wouldn't line up across instances; bound type imports unbound). **Seat exhaustion is alert/surface-only** — orders keep flowing. **i18n N/A** (admin UI hardcoded English). **Out of scope:** SaaS discovery, market benchmarks, usage-based reclamation |
| Drift / out-of-band reconciliation (AD) | 2026-07-14 | Closes the IGA write-only gap: ipSolis granted AD group membership fire-and-forget and never re-read it. New Beat pair in [`drift_reconcile.py`](worker/tasks/workflows/drift_reconcile.py) — `check_drift_schedule` (every minute, gated on `drift.enabled` + `drift.schedule_cron` via croniter, dedups on `drift.last_run`, mirrors the backup scheduler) enqueues `reconcile_drift`, which for every `ad_group` provisioned by a **`drift_monitor`** asset type (via `order_change_log` grants over active orders) reads **actual** membership (new [`list_ad_group_members`](worker/tasks/modules/target_executor.py) — paged `(&(objectClass=user)(memberOf=…))`, RFC-4515 escaped) and diffs it against what ipSolis granted **across all active orders** (so a group legitimately granted by a non-monitored type isn't mis-flagged). Two directions → `drift_findings` (new table, migration [`0004`](api/alembic/versions/0004_drift_reconciliation.py)): **missing_access** (granted, absent in AD → optional re-grant) and **out_of_band** (in AD, never granted, excludes the bind account → optional revoke). `drift.remediation_mode` = `detect_only` (default, record + alert) \| `auto_remediate` (writes AD via existing `_grant_ad_group`/`_revoke_ad_group`). Findings audited (→ SIEM) + best-effort email/Teams. **UI:** per-type *Monitor for access drift* toggle on the asset-type form; **Maintenance → Drift** tab (enable / cron / mode / run-now, `GET`+`PUT`+`run-now` on [`admin_maintenance.py`](api/app/routes/admin_maintenance.py)); live **Operations → Drift** tile ([`admin_operations.py`](api/app/routes/admin_operations.py) `drift` block replaces the old placeholder). **Verified end-to-end against the real test AD** (WinSRV1): out-of-band member detected in `detect_only` (no write) then revoked in `auto_remediate`; out-of-band removal of a granted member re-granted; finding→dashboard data path; PUT validation (bad mode 400) + template compile. **Fixed a bug found in review:** an open finding first recorded in `detect_only` was never remediated after switching to `auto_remediate` (remediation was gated on the finding being *new*) — `_record_finding` now returns `(id, is_new)` so auto acts on already-open findings. **Guard for a later slice:** out-of-band auto-revoke has no allowlist/break-glass for known manual service accounts yet (`detect_only` default covers v1). **Out of scope:** `entra_group` drift, non-AD targets. **i18n N/A** (admin UI hardcoded English) |
| Manager order-on-behalf (team ordering) | 2026-07-14 | **Part 1** — AD [`lookup_direct_reports`](api/app/utils/ad_lookup.py) (reverse `manager` lookup) + [`is_owner_managed_by`](api/app/utils/ad_lookup.py) verify helper; portal `GET /portal/my-team`; a team-picker in the order form ([`order_new.html`](api/app/templates/portal/order_new.html)) — the requester's direct reports as quick-select chips, any valid user still typeable, graceful when AD has none; i18n ×5. **Part 2** — the manager-approval short-circuit in [`portal.py`](api/app/routes/portal.py): when a manager orders for their own report (verified: requester == owner's AD `manager`), the manager approval is recorded auto-approved + `sod_exempt`; the order is then advanced by re-using the decision path's `_compute_bucket_state`/`_post_approval_dispatch` so it neither hangs in `pending_approval` nor dispatches without a required approval (other approvals — owner/rules/classification — still gate normally). **Verified** vs the real test AD (stefan→jupp) + the bucket-quorum advance decision across manager-only / +owner-pending / +owner-approved cases. Portal end-to-end walkthrough (log in as a manager, order for a report) is the operator's final manual check. **i18n:** portal strings in all 5 locales |
| Backup encryption at-rest | 2026-07-14 | Optional AES-256-CBC encryption of DB backups, opt-in via a new `BACKUP_ENCRYPTION_KEY` infra secret ([`config.py`](api/app/config.py) + [`.env.example`](.env.example)). When set, [`_run_backup_sync`](worker/tasks/modules/maintenance.py) pipes `pg_dump \| gzip \| openssl enc` → `*.sql.gz.enc`; `run_restore` decrypts the same way. The **`.enc` suffix is the single source of truth** — the api picks it ([`admin_maintenance.py`](api/app/routes/admin_maintenance.py) `_backup_suffix`, incl. pre-restore safety backup + widened `_SAFE_NAME`); the worker encrypts/decrypts on that signal. **Back-compat:** key unset → plaintext `.sql.gz` as before; restore auto-detects, so old backups still load. Key is kept **out of the DB** (app_config lives inside the dump) and must be carried to a fresh host — [DR-RUNBOOK](docs/DR-RUNBOOK.md)/`.de` updated. Read-only "Encrypted at-rest" badge on the Maintenance → Backups tab via `GET /schedule`. Verified on the compose stack: real pg_dump→encrypt→decrypt yields valid SQL (openssl `Salted__`, wrong key fails); plaintext path still produces a valid gzip. **Out of scope:** full app_config-at-rest encryption |
| Admin UI navigation restructure | 2026-07-14 | Flat ~25-link sidebar (internal scrollbar at laptop heights) reorganised in [`base.html`](api/app/templates/base.html): **Stufe 1** — collapsible `<details>` groups (Operate, Administration), only the active group opens (native, no JS); reports moved out of tiny footer links. **Stufe 2** — consolidated **hubs**: Inventory (Asset Definitions + Personal Assets), Automation (Runbooks + Modules + PS Modules), Reports (Access + Cost + Certifications + Leaver + Audit) each collapse to one sidebar entry, with a shared tab bar on the member pages (`_partials/hub_tabs_{inventory,automation,reports}.html`). Sidebar now ≈ Operate group + 3 hub links + Administration group → no scrollbar. All role gates preserved; Jinja macros (navlink/hublink/grpsummary) remove repetition. Verified against a live session: every page 200, correct tab + sidebar hub active-state across all member pages |
| Guided setup wizard | 2026-07-14 | New [`/ui/setup-wizard`](api/app/templates/ui/setup_wizard.html) page (superadmin) — a guided stepper over the existing [`/admin/setup/state`](api/app/routes/admin_setup.py) checklist: Essential + Recommended progress bars, per-step status/hint, **Configure→** links to the settings section, and inline **Test connection** buttons wired to the existing `*/test` endpoints (email/ad/teams/siem). Skippable, re-checkable; first-run setup now redirects here ([`admin_auth.py`](api/app/routes/admin_auth.py)) instead of a blank dashboard. Nav entry under Configuration (superadmin). Reuses the checklist + test endpoints — **no rebuild, no new data model, no new endpoints**. Verified on the compose stack (setup/state + AD test reachable, page serves). **i18n N/A** (admin UI hardcoded English) |
| Operations dashboard (fulfillment SLA) | 2026-07-14 | New [`admin_operations.py`](api/app/routes/admin_operations.py) `GET /admin/operations/summary` + dedicated [`/ui/operations`](api/app/templates/ui/operations.html) page (admin, own nav entry, separate from the capacity dashboard). Tiles: **failed** provisionings with aging + multi-select **batch retry** (`POST /admin/operations/retry-batch` wraps the existing single retry N-fold, same FAILED-only guard), **overdue approvals** (pending `order_approvals` past SLA), **upcoming expirations** (active orders within horizon), **stuck-in-progress** (transitional past threshold, informational). SLA thresholds configurable via `app_config` `ops.approval_sla_hours` / `ops.stuck_hours` / `ops.expiry_horizon_days` (defaults 48/2/7). Drift tile (4) is a graceful-degrade placeholder (`drift.available=false`) **pending the B1 drift task**. No new data model. Verified on the compose stack: summary + real data, config-override respected, batch-retry guards reject not-found/not-failed without triggering provisioning. **i18n N/A** (admin UI hardcoded English) |
| Config migration export/import (JSON) | 2026-07-14 | New [`admin_migration.py`](api/app/routes/admin_migration.py): `GET /admin/migration/export` downloads asset-types + asset-pool instances as one portable JSON doc (pool instances reference their type **by name**, not id); `POST /admin/migration/import?dry_run=` loads it **insert-only** (existing name skipped, never overwritten — safe re-import), reusing `validate_asset_type` + `aaudit`, imported instances start `Free`. Migration tab on the Maintenance page ([`maintenance.html`](api/app/templates/ui/maintenance.html)) with export download + file upload + dry-run preview. **No new data model.** Verified round-trip on the compose stack (export→dry-run→real→idempotent re-import; invalid-category + orphan-type paths). **Bundles** deferred (gated on the Onboarding-bundles entity). **i18n N/A** — admin UI is hardcoded English (`locales/*.json` are portal-only) |
| Point-in-time access report | 2026-07-14 | New [`admin_access_report.py`](api/app/routes/admin_access_report.py) `GET /admin/access-report` (auditor floor, `orders:read`): reconstructs the active access set **as of any past date** by replaying `order_change_log` — `DISTINCT ON (principal,target_type,identifier)` latest **successful** event, keep those whose latest is a `grant`; `?as_of=YYYY-MM-DD` (end-of-day UTC, live if omitted), `principal`/`asset_type_id` filters, JSON + CSV. UI page [`access_report.html`](api/app/templates/ui/access_report.html) + [`/ui/access-report`](api/app/routes/ui.py) + nav (mirrors the cost-report `?as_of=` pattern). **No new data model** — query over existing logs. Verified end-to-end on the compose stack incl. before/after-grant-date differential (0→1). **i18n N/A** — the admin UI is hardcoded English (`locales/*.json` are portal-only) |
| DR runbook (backup/restore) | 2026-07-14 | Bilingual [`docs/DR-RUNBOOK.md`](docs/DR-RUNBOOK.md) / [`.de.md`](docs/DR-RUNBOOK.de.md): fresh-host recovery (host → DB-only up → clean DB → `gunzip -c \| psql` → `alembic upgrade head`), what the pg_dump does/doesn't contain (plaintext `app_config` creds **are** in it; `API_SECRET_KEY`/externalized `vault://` secrets are **not**), `api_tokens` rotation, tick-off verification (email/AD/approval-link), externalized-secret case, rollback via pre-restore safety backup. Fixed docs drift in [`INSTALL.md`](docs/onboarding/INSTALL.md) (removed non-existent `from app.tasks import backup_database`, added restore pointers). Doc-only by design — **no automated restore test** (audit A4) |
| CI test gate + Playwright E2E | 2026-07-13 | New [`ci.yml`](.github/workflows/ci.yml) on push/PR to `dev` — three jobs: **ruff** (critical errors `E9,F63,F7,F82`), **unit** (`api/tests` on the runner in conftest's local mode — pure/mocked, imports `app.*`+`tasks.*`), **e2e** (headless Playwright vs. the `docker compose` stack). Host-side smoke suite in [`tests/e2e/`](tests/e2e/): health, admin login (+ negative), core-page nav, asset-type form, portal reachability; login handles both first-run setup and legacy `ADMIN_API_KEY`. Closes the "no test/lint gate before prelive" gap. **Deferred:** broaden ruff rules over time; deeper journeys (order-create needs real backends → intentionally out) |
| Provider-agnostic SSO (generic OIDC) | 2026-06-20 | Any compliant IdP via discovery doc (Entra, Okta, Ping, Google, Keycloak…); provider registry `idp.<id>.*`, parametric callback `/portal/auth/{id}/callback`, login picker, RP-initiated logout; retired `entra.py`/MSAL path; on-prem LDAP alongside. **Deferred:** SAML 2.0 + Okta OIN listing; SCIM provisioning is its own open task |
| GHCR prebuilt images | 2026-06 | CI build+push to ghcr.io on `v*.*.*` tags, public packages, `docker-compose.ghcr.yml`, `locales/`+`scripts/` baked into images, pull-count note (`docs/internal/metrics.md`); multi-arch arm64 intentionally skipped (amd64-only — on-prem is amd64) |
| Admin RBAC (slices 1–4) | 2026-04-26/27 | Per-user accounts, 5-tier role ladder, ACLs, SoD, lockout, password rotation |
| External secret management | 2026-04-26/30 | Vault, CCP, Azure KV, AWS SM, Conjur; AppRole/JWT; AssumeRole; migration tool |
| API tokens with scopes | 2026-04-26 | Bearer auth, 14-scope catalog, ServiceNow webhook, hard-delete purge |
| Audit log + SIEM | 2026-04-26/30 | Splunk HEC, Sentinel Logs Ingestion API, streaming-failure alerts |
| Conditional approval rules | 2026-04-26/30 | AND/OR/NOT visual editor, per-bucket supersession, escalation v2 |
| Per-classification approval routing | 2026-04-30 | Compliance officer + owner-of-record modes for PII/PHI/PCI |
| Access certification campaigns | 2026-04-30 | Slice 1+2: schema, admin CRUD, signed-token review URL, auto-revoke Beat task |
| Multi-instance HA | 2026-04-30 | Multi-replica API/worker docs, Postgres standby + failover docs, Beat-alive health probe |
| Portal SSO (Entra ID) | 2026-03-23 | MSAL, session middleware, `entra.mode` config, domain check |
| Portal auth `onprem_ldap` mode | 2026-06 | LDAP bind form, 5-locale i18n, `entra_with_onprem` removed (not implemented, no prod deployments) |
| Open Core model | 2026-04-xx | Community + Pro tiers, two Dockerfiles, license simplification, public mirror repo |
| PS Modules | 2026-04-30 | Linux compatibility flag, upload support, Gallery installer |
| Standalone Runbooks | — | Ad-hoc + cron-scheduled runbooks, execution history |
| Leaver Events / Certifications | — | Bulk lifecycle triggers, access certification campaigns |
| Cost reporting | — | Chargeback breakdown, cost threshold alerts |
| Deployment docs | 2026-06 | Full EN + DE deployment guide, sudo fixes, nginx template, docker-compose.prod.yml |
| Production overlay rename | 2026-06-25 | `docker-compose.prelive.yml` → `docker-compose.prod.yml` (hard rename, no alias). Updated CI (`deploy-prelive.yml`), `docker-compose.ghcr.yml` comments, `bootstrap-certs.sh`, README, DEPLOYMENT.md/.de.md (dropped "historical name" comments). Prelive *environment*/workflow names unchanged. **Action:** update LinPre1/LinPre3 `COMPOSE_FILE` env to the new filename |
