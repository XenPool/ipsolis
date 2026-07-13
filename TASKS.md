# ip·Solis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.

---

## Open Tasks

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

---

### [open] Access Targets — only `ad_group` is implemented
Of the four **Access Target** types offered in the asset-type form, only **AD Group**
(`ad_group`) works end-to-end. The other three are now shown as **"(coming soon)"** and
**disabled** in the UI ([`asset_type_form.html`](api/app/templates/ui/asset_type_form.html),
all three dropdown render paths) so operators can no longer save targets that fail silently
at provision time. Current backend state in
[`target_executor.py`](worker/tasks/modules/target_executor.py):

- **`entra_group`** — stub: `_grant_entra_group` / `_revoke_entra_group` raise
  `NotImplementedError`. To finish: implement grant/revoke via **Microsoft Graph**
  (Application Permission `GroupMember.ReadWrite.All`) for Entra cloud-only security groups
  on asset types defining `{"type": "entra_group", "identifier": "<group-id>"}`. Fits the
  existing Entra/OIDC stack; separate sprint.
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
