# XenPool IT Selfservice – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`
Add new tasks at the top.

---

## Open

### [done] Commit & Cleanup Pending Changes — Prio 0 (hygiene) (2026-03-23)
- Committed 23 files (4cada00): migrations 0017/0018, capacity.py, xenserver scripts,
  SCCM scripts, all modified routes/templates/models/workers

### [done] Beat-Scheduler → migrate to dynamic_runner (2026-03-23)
- `check_expiring_assets` now creates a `delete` order per expired asset (copies
  `provisioned_state` from the provision order for deterministic revoke) and dispatches
  `dynamic_runner.run` instead of the hardcoded `vdi_reclaim.run`
- Original provision order is immediately set to `expired`; the new delete order
  progresses through `dynamic_runner` with the asset type's configured runbook/strategy
- Reminder email logic unchanged
- Note: a `delete` runbook must be configured per asset type in the Admin UI for
  `runbook_only` / `composite` asset types; `group_only` types work without a runbook

### [done] Legacy Workflow Cleanup — Prio 1b (2026-03-23)
- `check_expiring_assets` moved into `dynamic_runner.py` (new task name:
  `tasks.workflows.dynamic_runner.check_expiring_assets`); beat_schedule updated
- Deleted: `vdi_provision.py`, `vdi_modify.py`, `vdi_reclaim.py`
- Removed from `__init__.py`: legacy includes + task_routes entries

### [done] Portal Authentication — Entra ID SSO (2026-03-23)
- `msal` added to `api/requirements.txt`
- `SessionMiddleware` added to `main.py` (signed cookie, 8h TTL)
- `api/app/utils/entra.py` — MSAL helper (auth URL, token exchange, domain check)
- `api/app/routes/auth.py` — `/portal/login`, `/portal/auth/callback`, `/portal/logout`
- `api/app/routes/portal.py` — `require_portal_auth` dependency on all routes; dev bypass active
- `base_portal.html` — user name chip + Sign out link in nav bar
- `portal/auth_error.html` — error page for login failures
- `api/app/templates/ui/settings.html` — "Entra ID / Azure AD" section in Identity & Directory tab
- `POST /admin/config/entra/test` — verifies credentials via client-credentials token flow
- Migration 0019 — seeds 6 `entra.*` config keys (`entra.mode` defaults to `disabled`)
- Two-phase rollout: test with cloud-only Entra accounts now; switch to hybrid after Entra Connect setup

### [open] Entra ID Connect / Cloud Sync setup — infrastructure (no code change needed)
Sync `xenpool.local` on-prem users to the Entra ID tenant so they can use portal SSO with
their existing domain credentials. Pure Windows Server / Azure infrastructure task.
- [ ] Install Entra ID Connect (or Entra Cloud Sync agent) on a domain-joined server
- [ ] Configure UPN suffix (`xenpool.de`) for synced accounts
- [ ] Verify synced users can log into the portal (no code change required)

### [open] Cloud group management via Microsoft Graph — future
Extend `target_executor` to manage Entra cloud-only security groups for asset types
that define `{"type": "entra_group", "group_id": "..."}` targets.
Requires Microsoft Graph API integration (separate sprint).

### [done] Basic Tests (Happy Path) — Prio 3 (2026-03-24)
- `pytest>=8.0.0` + `pytest-asyncio` added to `api/requirements.txt`
- `api/tests/conftest.py` — adds `worker/` to sys.path; worker code imported without Celery/Redis
- `api/tests/test_happy_path.py` — 14 tests, 31 total passing:
  - `_final_status` for all 4 actions (provision/delete/modify/extend)
  - `_render_params` template substitution (hit/miss/literal/multi-key)
  - Runbook lookup: found→provisioned, not-found→failure, inactive→failure, modify-noop
  - Targets mode: group_only provision→provisioned, delete access_only→revoked
- `docker-compose.yml`: added `./api/tests` and `./worker` volume mounts to api service for in-container test runs
- Run: `docker compose exec api python -m pytest tests/ -v`

---

## Done

### [done] SCCM VDI Group Configuration Script (2026-03-23)
- `scripts/sccm/Configure-VDI-Groups.ps1` — executed during SCCM Task Sequence setup
- Creates `XenPool-VDI-<hostname>-RDP-Users` and `XenPool-VDI-<hostname>-ADM-Users` in `OU=VDI,OU=XenPool GmbH,DC=xenpool,DC=local` if not present
- Assigns RDP group → local `Remote Desktop Users`; ADM group → local `Administrators`
- Dual-channel logging: Windows Application Event Log (source `XenPool-VDI-Setup`) + `C:\Windows\debug\Configure-VDI-Groups.log`
- Returns exit code 0/1 so SCCM TS can detect failures

### [done] XenServer Script Library — VMware conversions (2026-03-16)
- `XenServer - VM reboot or startup (gracefully)` (ID 10)
- `XenServer - VM change boot order (disk-cd-net)` (ID 11) — HVM `hvm_boot_params["order"] = "cdn"`
- `XenServer - VM change boot order (net-cd-disk)` (ID 12) — HVM `hvm_boot_params["order"] = "ndc"`
- `XenServer - VM shutdown (gracefully)` (ID 13) — CleanShutdown + HardShutdown fallback
- `XenServer - VM stop (force)` (ID 14) — HardShutdown with retry logic
- All scripts: pure ASCII (no Unicode), `$null` on left side of comparisons, stored in DB + `scripts/xenserver/`
- Note: no XenServer Tools update equivalent exists in the SDK (guest-side operation only)

### [done] XCP-ng / XenServer Hosting Infrastructure (2026-03-16)
- Settings page: vSphere + XenServer credential sections (saved to `app_config`)
- Migration 0017: seeds `vsphere.*` and `xenserver.*` config keys
- Module editor: auto-injects hosting vars (`XenServerHost` etc.) into test runs
- `dynamic_runner`: exposes `config.xenserver.*` / `config.vsphere.*` in runbook ctx
- Script: `XenServer - VM reboot or startup (gracefully).ps1` (XCP-ng equivalent of VMware script)
- PS preamble: SSL cert bypass injected globally (self-signed cert support for XCP-ng/vSphere)
- Test runner: removed `-NonInteractive`, added `input="Y\n"` to auto-accept cert prompts
- Test runner: `param_schema` defaults auto-merged into test params (no manual JSON required)

### [done] PS Module Manual Upload — non-Gallery SDKs (2026-03-16)
- Migration 0018: `source_type` + `upload_data BYTEA` columns on `ps_modules`
- API: `POST /admin/ps-modules/{id}/upload` — stores zip in DB, triggers install
- Worker: `_install_from_upload()` — extracts zip to `~/.local/share/powershell/Modules/`, reads version from `.psd1`
- UI: source toggle (Gallery / Manual Upload), Upload zip button per row, `awaiting_upload` status badge

### [done] Pool Capacity Enforcement + Display (2026-03-16)
- `api/app/utils/capacity.py`: `enforce_pool_capacity()` — HTTP 409 if pool full
- Orders + webhook routes: pre-flight capacity check for PROVISION actions
- Asset types list: shows `X / Y in use` with color coding for capacity_pooled types

