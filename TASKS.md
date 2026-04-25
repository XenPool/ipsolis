# Ipsolis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.
Strategic roadmap up top; smaller polish/gap items in the middle; pre-existing infra
and historical "done" entries at the bottom.

---

## Strategic — Enterprise-class roadmap

These are the gaps that block ipSolis from being drop-in for a 5,000-seat regulated
enterprise. Order = priority (procurement-blocker first).

### [open] Admin RBAC — Prio 0 (show-stopper)
Today the admin UI is binary: `X-Admin-Key` or admin session = god mode.
Roles to design: `superadmin`, `admin`, `approver`, `auditor` (read-only),
`helpdesk` (revoke-only). Per-asset-type ACLs as a stretch goal so platform
owners can be delegated without seeing other teams' configurations.
SoD requirement: configurer of an asset type must not also approve their own
access requests against it.
- [ ] Roles enum + `admin_users` table (or `admin_role` column)
- [ ] Permission check decorator/dependency for each admin endpoint
- [ ] Admin UI — login flow shows role; nav items hide what the role can't reach
- [ ] Audit-log entry includes the role of the actor
- [ ] Migration to map all existing `X-Admin-Key` use to `superadmin`

### [open] External secret management — Prio 0 (show-stopper)
Today AD password / vSphere creds / SMTP password / Entra client secret all
sit in `app_config` as plaintext. The `is_secret=true` flag only hides them
in the UI. Add a `SecretBackend` abstraction with implementations for
`db` (current), `vault`, `azure_keyvault`, `aws_secretsmanager`. Resolution
goes through the backend on read — schema unchanged but values may be
references like `vault://secret/data/ipsolis/ad/password`.
- [ ] `SecretBackend` interface + `db` (no-op) + `vault` (HashiCorp Vault) impl
- [ ] Settings: backend choice + connection params
- [ ] `app_config.value` resolver routes `is_secret=true` rows through the backend
- [ ] One-shot migration tool: move existing plaintext secrets into the chosen backend

### [open] API tokens with scopes — Prio 0 (show-stopper)
Replace single shared `X-Admin-Key` with per-integration tokens that have
scoped permissions, expiry, last-used timestamp, revocation UI. Each token
gets a `name`, `created_by`, `scopes[]` (e.g. `orders:read`, `orders:write`,
`asset_types:read`, `webhook:in`), `expires_at`. ServiceNow webhook secret
becomes one such token.
- [ ] `api_tokens` table + ORM
- [ ] Issuance endpoint (creates random token, returns once, stores SHA256)
- [ ] Header `Authorization: Bearer <token>` accepted alongside legacy `X-Admin-Key`
- [ ] Admin UI: token list + create + revoke; show last 4 chars after issuance
- [ ] Per-endpoint scope check decorator
- [ ] Migration: the legacy `ADMIN_API_KEY` becomes a system token with `*` scope

### [open] Tamper-evident audit + SIEM export — Prio 0 (show-stopper)
`audit_log` is a normal table — admin with DB access (or SQL injection) can
`DELETE` rows. Add append-only via DB-role grants (revoke `DELETE` from the
app role on `audit_log`), plus a Beat task that streams new rows to a
configurable webhook (Splunk HEC / Sentinel / ELK / generic) with HMAC
signature.
- [ ] Migration: revoke DELETE/UPDATE on `audit_log` from the app DB role
- [ ] Settings: SIEM endpoint URL + HMAC secret + format (JSON / CEF)
- [ ] Beat task: stream new audit rows since `last_streamed_id` every minute
- [ ] On streaming failure: backoff + alert via existing health-alert email path

### [open] Multi-instance HA — Prio 0 (show-stopper)
Single api / single worker / single Postgres / single Beat. Beat especially
is a SPOF — default Celery Beat doesn't lock, two beats = duplicate
dispatches. Need documented multi-replica deployment.
- [ ] Replace default Celery Beat with `celery-singleton` or Redis-locked beat
- [ ] Document Postgres standby setup (logical replication or pgBackRest)
- [ ] Multi-replica api: ensure session storage is Redis-backed (currently
      cookie-signed — already stateless, just verify)
- [ ] Multi-replica worker: prefork already works, just document scaling
- [ ] Health probe that detects "Beat is alive somewhere" via Redis heartbeat

---

## Differentiators (Prio 1) — table-stakes for upper-mid market

### [open] Access certification campaigns — Prio 1
Quarterly "managers must re-confirm their team's access" workflow with email
reminders, escalation, auto-revoke on no-response. Hard requirement for ISO27001 / SOX / PCI audits.
- [ ] `certification_campaigns` table (created_at, scope, due_at, status)
- [ ] Beat task: scan active orders matching campaign scope, create review tasks
- [ ] Manager portal page: list pending reviews with one-click confirm/revoke
- [ ] Email reminders T-7d / T-1d / overdue; escalation to manager's manager

### [open] Approval-flow sophistication — Prio 1
N-of-M approvers, sequential vs parallel, escalation if no response in X
hours, delegation when approver is OOO, conditional rules (e.g., "extend
> 90 days needs CISO", "PCI-tagged types need both manager + app owner").
- [ ] Schema: `approval_rules` JSONB on asset_type extending current `approval_owners`
- [ ] Runtime evaluator that resolves rules → approval steps
- [ ] UI: rule-builder (avoid full DSL; predefined patterns)

### [open] HR feed + SCIM — Prio 1
Auto-deprovision on `LeaverEvent` from Workday/SAP HR; SCIM in/out so Okta /
Ping / SailPoint can drive ipSolis as an authoritative target.
- [ ] SCIM 2.0 endpoint (`/scim/v2/Users`, `/scim/v2/Groups`)
- [ ] HR webhook receiver with vendor-specific adapters
- [ ] Leaver flow: revoke all active orders for the user, audit

### [open] Observability — Prometheus + OpenTelemetry — Prio 1
- [ ] `/metrics` endpoint (request count, latency histograms, queue depth)
- [ ] OpenTelemetry tracing with auto-instrumentation for FastAPI, Celery, SQLAlchemy
- [ ] Sample Grafana dashboards: provisioning latency p50/p95, queue depth, error rate

### [open] Cost / chargeback per asset type — Prio 1
- [ ] Asset type fields: `monthly_cost`, `cost_center`, `currency`
- [ ] Order detail shows projected cost
- [ ] Monthly export: rows per cost-center × asset-type × user-count
- [ ] Optional alert when monthly cost exceeds threshold

---

## Polish & smaller gaps (Prio 2)

### [done] `max_per_user` for pooled types — Prio 2 (2026-04-25)
Per-user quota now enforced everywhere a PROVISION order can be created
(public API, ServiceNow webhook, self-service portal). Quota covers personal
and pooled assignment models; `dedicated_shared` is exempt because everyone
shares a single instance.
- UI: `max_per_user` input lifted out of the personal-only section in the
  asset-definition form; visible for `assigned_personal` + `capacity_pooled`,
  hidden only for `dedicated_shared`. Helper text explains the active-status
  set the count is taken over.
- Runtime: new `enforce_max_per_user()` in `api/app/utils/capacity.py`
  returns HTTP 409 with a descriptive detail when the user is at the limit.
- Wired into `api/app/routes/orders.py` (after `enforce_pool_capacity`),
  `api/app/routes/webhook.py` (ServiceNow path), and
  `api/app/routes/portal.py` (renders error inline via `_render_error`).
- Bonus correctness fix: `_ACTIVE_STATUSES` in `capacity.py` now includes
  `PENDING_APPROVAL` and `SCHEDULED` — closes a hole that let scheduled and
  approval-pending orders bypass both pool capacity *and* the per-user quota.
- Counting uses case-insensitive `user_email` match so Outlook-style casing
  variants don't yield a fresh slot.

### [done] `is_active` flag on asset definitions — Prio 2 (2026-04-25)
Admins can now deprecate without delete. Inactive types are hidden from the
portal catalog (`/portal/orders/new`) but stay visible in the admin list with
an "Inactive" badge so historical orders, audit, and runbook configs stay coherent.
- Migration `0049_asset_type_is_active.py` — adds `is_active BOOLEAN NOT NULL DEFAULT true` column.
- ORM `AssetType.is_active` (`api/app/models/asset.py`).
- Pydantic `AssetTypeCreate` / `AssetTypeUpdate` / `AssetTypeRead` carry `is_active`.
- Admin route POST/PUT/clone honor the field; clone preserves the source's flag.
- Audit snapshot `_type_snap()` includes `is_active` so deprecation events are diffable.
- Form: new "Active" checkbox with explainer in the Identity section, default-checked.
- List: "Inactive" badge + 60% row opacity on deprecated rows.
- Portal: catalog list / re-render error path filter `WHERE is_active = true`.
- Verified end-to-end: PUT `is_active=false` removes from catalog, admin list keeps it with badge.

### [open] Long-form `help_text` per asset definition (markdown) — Prio 2
Requesters see only the one-line description in the catalog. Admins routinely
want a paragraph: "this VDI ships with Office 2024, IntelliJ, AutoCAD; expect
2-min provision; contact ITops@…". Render at order time as sanitized markdown.

### [open] Microsoft Teams / Slack approval cards — Prio 2
One-click approve from a chat card, signed JWT in the action link so the
approver doesn't need to log into the portal.

### [open] Field-level data classification — Prio 3
Tag fields as PII / PHI / PCI; drive approval routing and audit retention.

### [open] Catalog search & filter in the portal — Prio 3
Search box + category filter on `/portal/orders/new`. Becomes important once
a customer has > ~30 asset definitions.

### [open] In-app onboarding / guided tour — Prio 3
First-run admin walkthrough; drop-in for new admins.

---

## Pre-existing open tasks

### [open] Entra ID Connect / Cloud Sync setup — infrastructure (no code change needed)
Sync `xenpool.local` on-prem users to the Entra ID tenant so they can use portal SSO with
their existing domain credentials. Pure Windows Server / Azure infrastructure task.
- [ ] Install Entra ID Connect (or Entra Cloud Sync agent) on a domain-joined server
- [ ] Configure UPN suffix (`xenpool.de`) for synced accounts
- [ ] Verify synced users can log into the portal (no code change required)

### [open] Cloud group management via Microsoft Graph — future
Extend `target_executor` to manage Entra cloud-only security groups for asset types
that define `{"type": "entra_group", "group_id": "..."}` targets. Requires
Microsoft Graph API integration (separate sprint).

---

## Done

### [done] Portal Authentication — Entra ID SSO (2026-03-23)
- `msal` added to `api/requirements.txt`
- `SessionMiddleware` added to `main.py` (signed cookie, 8h TTL)
- `api/app/utils/entra.py` — MSAL helper (auth URL, token exchange, domain check)
- `api/app/routes/auth.py` — `/portal/login`, `/portal/auth/callback`, `/portal/logout`
- `api/app/routes/portal.py` — `require_portal_auth` dependency on all routes; when `entra.mode = disabled` the portal is open with a shared anonymous identity
- `base_portal.html` — user name chip + Sign out link in nav bar
- `portal/auth_error.html` — error page for login failures
- `api/app/templates/ui/settings.html` — "Entra ID / Azure AD" section in Identity & Directory tab
- `POST /admin/config/entra/test` — verifies credentials via client-credentials token flow
- Migration 0019 — seeds 6 `entra.*` config keys (`entra.mode` defaults to `disabled`)

### [done] Beat-Scheduler → migrate to dynamic_runner (2026-03-23)
- `check_expiring_assets` now creates a `delete` order per expired asset
  (copies `provisioned_state` from the provision order for deterministic revoke)
  and dispatches `dynamic_runner.run` instead of the hardcoded `vdi_reclaim.run`
- Original provision order is immediately set to `expired`; the new delete
  order progresses through `dynamic_runner` with the asset type's configured
  runbook/strategy
- Note: a `delete` runbook must be configured per asset type in the Admin UI
  for `runbook_only` / `composite` asset types; `group_only` types work without

### [done] Legacy Workflow Cleanup — Prio 1b (2026-03-23)
- `check_expiring_assets` moved into `dynamic_runner.py`; beat_schedule updated
- Deleted: `vdi_provision.py`, `vdi_modify.py`, `vdi_reclaim.py`
- Removed from `__init__.py`: legacy includes + task_routes entries

### [done] Basic Tests (Happy Path) — Prio 3 (2026-03-24)
- `pytest>=8.0.0` + `pytest-asyncio` added to `api/requirements.txt`
- `api/tests/conftest.py` — adds `worker/` to sys.path
- `api/tests/test_happy_path.py` — 14 tests, 31 total passing
- `docker-compose.yml`: added `./api/tests` and `./worker` volume mounts
- Run: `docker compose exec api python -m pytest tests/ -v`

### [done] SCCM VDI Group Configuration Script (2026-03-23)
- `scripts/sccm/Configure-VDI-Groups.ps1` — executed during SCCM Task Sequence setup
- Creates RDP/ADM groups in `OU=VDI,OU=XenPool GmbH,DC=xenpool,DC=local`
- Dual-channel logging: Windows Event Log + `C:\Windows\debug\Configure-VDI-Groups.log`

### [done] XenServer Script Library — VMware conversions (2026-03-16)
- `XenServer - VM reboot or startup (gracefully)` (ID 10)
- `XenServer - VM change boot order (disk-cd-net)` (ID 11) — `hvm_boot_params["order"]="cdn"`
- `XenServer - VM change boot order (net-cd-disk)` (ID 12) — `"ndc"`
- `XenServer - VM shutdown (gracefully)` (ID 13) — CleanShutdown + HardShutdown fallback
- `XenServer - VM stop (force)` (ID 14) — HardShutdown with retry logic

### [done] XCP-ng / XenServer Hosting Infrastructure (2026-03-16)
- Settings page: vSphere + XenServer credential sections
- Migration 0017: seeds `vsphere.*` and `xenserver.*` config keys
- Module editor: auto-injects hosting vars
- `dynamic_runner`: exposes `config.xenserver.*` / `config.vsphere.*`
- PS preamble: SSL cert bypass injected globally

### [done] PS Module Manual Upload — non-Gallery SDKs (2026-03-16)
- Migration 0018: `source_type` + `upload_data BYTEA` columns on `ps_modules`
- API: `POST /admin/ps-modules/{id}/upload`
- Worker: `_install_from_upload()` — extracts zip to `~/.local/share/powershell/Modules/`

### [done] Pool Capacity Enforcement + Display (2026-03-16)
- `api/app/utils/capacity.py`: `enforce_pool_capacity()` — HTTP 409 if pool full
- Orders + webhook routes: pre-flight capacity check for PROVISION actions
- Asset types list: shows `X / Y in use` with color coding for capacity_pooled types
