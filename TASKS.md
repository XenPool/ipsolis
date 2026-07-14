# ip·Solis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.

---

## Open Tasks

> Tasks below down to _Portal accessibility_ were added on 2026-07-14 from the codebase audit
> ([`AUDIT-FINDINGS.md`](AUDIT-FINDINGS.md)). Rough priority order, highest first.

### [open] Drift / out-of-band reconciliation (AD group membership) — top priority

The IGA backbone gap: [`target_executor.py`](worker/tasks/modules/target_executor.py) is
effectively **write-only** (fire-and-forget grant/revoke); its only LDAP read resolves user DNs,
never group membership. [`Order.provisioned_state`](api/app/models/order.py#L127-L128) is a stored
snapshot that is never re-read against live AD, and no Beat job reconciles the two. So ipSolis is
fire-and-forget, not a system of record — it can't tell you if someone was added to a managed group
out of band, or removed from one it granted.

**Scope:**
- New Beat task (scan-and-act pattern, like `certification_reminders.scan_and_remind`) that reads
  **actual** AD group membership — a new `pagedsearch` on the group `member` attribute, extending
  the user-DN read at [`target_executor.py:46-63`](worker/tasks/modules/target_executor.py#L46-L63)
  — and diffs it against `Order.provisioned_state`.
- **Two toggle levels:** global master switch `drift.enabled` (default **off**, opt-in) **plus**
  per-asset-type opt-in; scheduler `drift.schedule_cron`. Config pattern mirrors the backup
  scheduler (`backup.enabled` / `backup.schedule_cron`,
  [`admin_maintenance.py:834-887`](api/app/routes/admin_maintenance.py#L834-L887)).
- `drift.remediation_mode` = **`detect_only`** (default; alert only) | **`auto_remediate`**
  (correct via the existing [`_grant_ad_group` / `_revoke_ad_group`](worker/tasks/modules/target_executor.py#L190) handlers).
- **Both directions:** missing access (in `provisioned_state`, not in AD → optional re-grant) and
  out-of-band access (in AD, never granted by ipSolis → optional revoke).
- Alerting reuses existing channels (email / Teams card / SIEM `post_webhook`).
- Only `ad_group` for now (`entra_group` is a stub — see the Access Targets task).
- **Guard for a later slice:** auto-remediate of out-of-band members revokes manually-added AD
  members — needs an allowlist / break-glass concept (known service accounts); the `detect_only`
  default covers v1.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** `entra_group` drift (until entra_group provisioning lands); non-AD target types.

---

### [open] Software license & contract lifecycle

Cost reporting today answers "what did access cost" (chargeback) but has no notion of the
*contract* behind an asset type. **Not** the commercial product `.lic` licensing system
([`admin_license.py`](api/app/routes/admin_license.py)) — that is product/tier gating; this tracks
*customer* software contracts.

**Scope:**
- New entity `License`/`Contract`: vendor, product, `contract_value`, billing interval,
  `licensed_seats` (nullable = unlimited), `start`/`renewal_date`, `notice_period_days`,
  `auto_renew`, notes.
- **Cardinality 1 License : N AssetTypes** — [`AssetType`](api/app/models/asset.py) gets a
  `license_id` FK (0..1). **Consumption = sum of active** (non-revoked/non-expired)
  [`Order`](api/app/models/order.py) rows across all bound types (derived, not stored — same logic
  as `max_per_user`). Surface over-/under-allocation.
- **Seat exhaustion: surface + alert only** — orders keep flowing; the contract is decoupled from
  provisioning (no order-time block/warning).
- **Cost: full chargeback integration** — contract cost flows into cost allocation (seat price ×
  consumption per cost center). NB: this is the largest part of the task — a real change to the
  cost report, not just an informational field.
- Renewal-reminder Beat task at `renewal_date − notice_period_days`; reuse SIEM/audit/email emit.
  Admin CRUD + binding on [`asset_type_form.html`](api/app/templates/ui/asset_type_form.html);
  contract/renewal view in the cost report.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** SaaS discovery; cost-per-seat market benchmarks; usage-based (last-login)
reclamation (needs a usage signal ipSolis does not collect).

---

### [open] Manager order-on-behalf (team ordering)

Ordering an asset whose end-user differs from the requester already works (owner ≠ requester:
[`order.py:77-79`](api/app/models/order.py#L77-L79), `is_deputy` at
[`portal.py:560`](api/app/routes/portal.py#L560)), but there is no manager→team relationship — any
requester can name any directory user as owner. Distinct from approval-delegation (which only
re-routes *approval decisions*).

**Scope:**
- **Team source: AD `directReports`** (reverse of the `manager` attribute) — reuse the existing AD
  lookup (`lookup_manager`) in reverse.
- Team picker in the portal create form (shows the requester's own team first) but any valid
  directory user remains selectable as owner.
- **Manager approval counts as implicitly satisfied** when the requester orders for their own report
  — **guard: only when the AD manager relationship is verified** (requester == AD `manager` of the
  owner); otherwise the full approval flow runs. Implemented via the existing `sod_exempt` mechanic
  ([`approval.py:63`](api/app/models/approval.py#L63)), not a new skip path.
- No new data model — a directReports lookup + server-side manager-verify on top of the existing
  owner/`is_deputy` machinery.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** org-hierarchy management UI; multi-level / cross-team delegation.

---

### [open] Slack approval delivery (delta)

Microsoft Teams Adaptive-Card delivery already runs in parallel with email on every approval path
([`teams_notify.py`](worker/tasks/modules/teams_notify.py); dual delivery in
[`dynamic_runner.py:1416-1484`](worker/tasks/workflows/dynamic_runner.py#L1416-L1484)). Only **Slack**
is missing — this is a thin delta, not a new channel framework.

**Scope:**
- New `slack_notify.post_message()` (Slack incoming webhook / Block Kit) as a **second delivery
  branch** in `deliver_approval_notification`, mirroring the Teams branch.
- Config keys `slack.mode` / `slack.webhook_url` (pattern `teams.*`), `POST /config/slack/test`,
  setup-checklist item — all analogous to Teams.
- Channel-agnostic signed token unchanged; reminder / cost-threshold / certification paths inherit
  the channel like Teams. Teams remains untouched.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** interactive approve-in-Slack actions — link out to the signed-token URL like Teams.

---

### [open] Signed attestation artifacts: handover & revocation certificates

Two ISO-27001-relevant evidence artifacts sharing one mechanism (the signed-token URL already proven
by the access-certification review link, [`certification_token.py:45-59`](api/app/utils/certification_token.py#L45-L59)).
Both hang off existing [`Order`](api/app/models/order.py) lifecycle transitions — no new lifecycle
states. **NB (audit correction):** there is **no PDF generation** in the repo — artifacts are
signed HTML, not PDF.

**Scope:**
- **Format: signed-token HTML page** (like the certification review URL) — **no PDF library**
  (none exists; PDF export is a later follow-up). Archival via browser print. 1:1 reuse of the
  signed-token + Jinja mechanics.
- **Handover (Übergabeprotokoll) on `provisioned`:** optional receipt/AUP acknowledgment via signed
  link (asset type, recipient, config snapshot, optional AUP); acknowledgment persisted +
  audit-logged.
- **Revocation/disposal certificate on `revoked`/`expired`:** optional signed HTML attestation of
  removal/retirement (removed from AD group X, VM deleted, date Y) for audit/disposal evidence;
  hangs off the existing revoke path.
- Per-asset-type flags `requires_handover_ack` / `emit_revocation_certificate` (default **off**);
  admin view of outstanding/completed acks; overdue reminder via the existing Beat pattern. Nothing
  is blocked.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)) + artifact text strings.

**Out of scope:** server-generated PDF export (later follow-up if a customer needs archival PDFs);
qualified/eIDAS e-signatures; QR-tagged physical inventory.

---

### [open] Guided setup wizard (delta)

The diagnostics page and all per-integration "test connection" endpoints already exist; a live setup
**checklist** ([`admin_setup.py:44-171`](api/app/routes/admin_setup.py#L44-L171)) already derives what
is configured. Only the *guided flow* is missing — today the checklist just links out to scattered
settings sections.

**Scope:**
- **Guided multi-step flow on its own page** after first-admin creation (Branding → SMTP → AD →
  SSO → …), each step with an **inline test** via the existing `*/test` endpoints
  ([`admin.py:236`](api/app/routes/admin.py#L236) AD, `:309` IdP, `:460` SIEM, `:562` Teams,
  `:607` secret-store, `:702` SCCM, `:1653` SMTP).
- **Skippable / re-callable anytime**; shown until the checklist `essential` items are done
  (progress source = the existing `setup/state`). Guides, does not gate.
- **Do not rebuild** the diagnostics page / test endpoints — they exist.
- No new data model.

**Follow-up:** every new UI string in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** rebuilding diagnostics/test endpoints; mandatory gating of the main UI behind the
wizard.

---

### [open] Backup encryption at-rest

Surfaced by the DR audit: credentials are stored **plaintext** in `app_config`
([`config.py:21`](api/app/models/config.py#L21); `is_secret` only masks the UI, it does not
encrypt), so the pg_dump backup files ([`maintenance.py:165-274`](worker/tasks/modules/maintenance.py#L165-L274))
contain AD/SMTP passwords in cleartext. A GDPR/security hardening gap — the backup file is a
credential store.

**Scope:**
- Encrypt backup files at-rest — symmetric, key stored **separately** (NOT `API_SECRET_KEY`, else a
  key-in-the-same-restore problem) — or, at minimum, a documented requirement to encrypt the backup
  volume/target.
- Restic/Borg-style encrypted backup transport as the lightweight documented entry point.
- Optional/complementary (note as an option, not a v1 must): encrypt `app_config` secrets at-rest
  (larger change touching the secret-resolver path).
- Thematically GDPR (adjacent to the shipped audit-retention feature) but **not** retention — a
  separate small task.

**Follow-up:** bilingual docs (EN + DE, `<name>.md` / `<name>.de.md`).

**Out of scope:** full `app_config`-at-rest encryption in v1.

---

### [open] Portal accessibility (BITV 2.0 / EN 301 549)

Public sector is in the target market, which makes accessibility a tender gate. There is no a11y
work today; `<html lang="en">` is hardcoded despite the DE/EN switcher
([`base_portal.html:2`](api/app/templates/portal/base_portal.html#L2)) — itself a BITV `lang`
conformance gap.

**Scope:**
- **Structural-first at the shared choke points** ([`base_portal.html`](api/app/templates/portal/base_portal.html)
  + `_partials/language_switcher.html`): dynamic `lang` binding (not hardcoded `en`), skip link,
  landmark roles, focus/keyboard operability, form labels. Full WCAG 2.1 AA follows iteratively.
- **Portal only** (end-user surface) — **admin UI excluded** (operator tool, generally not in tender
  scope).
- **This version is not a conformance claim** — it closes the structural basics; the formal
  Barrierefreiheitserklärung + external BITV test is a **separate follow-up task** when a concrete
  tender arises.

**Follow-up:** new visible strings (skip-link text, …) in all 5 locale files
([`en`](locales/en.json) / [`de`](locales/de.json) / [`fr`](locales/fr.json) /
[`es`](locales/es.json) / [`it`](locales/it.json)).

**Out of scope:** admin-UI accessibility; the formal accessibility statement + external BITV test
(separate follow-up); full AA conformance certification.

---

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
