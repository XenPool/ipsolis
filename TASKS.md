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

### [open] Legacy Workflow Cleanup — Prio 1b (depends on Beat-Scheduler migration)
After `check_expiring_assets` is migrated, the three hardcoded workflow files are fully obsolete.
- [ ] Delete `worker/tasks/workflows/vdi_provision.py`
- [ ] Delete `worker/tasks/workflows/vdi_modify.py`
- [ ] Delete `worker/tasks/workflows/vdi_reclaim.py`

### [open] Portal Authentication — Prio 2
The portal is currently fully open (email input only, no session/auth).
For production use within the company network, at least one of the following options:
- [ ] Option A: Entra ID / OIDC (SSO via `msal` or `authlib`)
- [ ] Option B: Simple IP allowlist + session cookie (faster for internal MVP)
Decision pending.

### [open] Basic Tests (Happy Path) — Prio 3
No automated tests exist yet.
- [ ] pytest setup in `api/tests/`
- [ ] Happy path: create order → dynamic_runner completes → status = delivered
- [ ] Runbook lookup: correct runbook found for asset type + action

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

