# ip·Solis – Edition Feature Matrix

This document defines the feature split across the two editions of ip·Solis:
**Community** (open-source, AGPL-3.0) and **Pro** (commercial).

## Guiding Principles

- The Community Edition is **fully functional** for on-premises IT asset lifecycle management — not a crippled demo.
- Pro features target organizations requiring advanced operational integrations: ITSM, identity sync, SIEM, and access certification workflows.
- Edition split is implemented via **separate Docker images** — the Community image simply does not contain the Pro-only files. There is no runtime feature gating; absent routes return HTTP 404, not HTTP 403.
- Legacy license edition values (`business`, `enterprise`, `professional`) are normalised to `pro` on load for backwards compatibility with older signing tools.

---

## Community Edition (AGPL-3.0)

### Self-Service Portal
- Asset request, status tracking, extend, return
- "My IT" dashboard (active assets overview)
- Multi-language UI (EN, DE, FR, ES, IT)
- Entra ID (Azure AD) single sign-on
- Deputy support (order on behalf of another user)
- Catalog search and category filter
- Long-form help text per asset definition (admin-authored markdown)
- Per-order cost projection shown to the requester before submission

### Approval Workflows
- Manager approval (auto-resolved from Active Directory)
- Application owner approval (second approval tier)
- Re-approval on asset modification (configurable per asset type)
- N-of-M approvals + conditional approval rules (per-bucket quorum with recursive AND/OR/NOT rule editor)
- Per-classification approval routing (PII / PHI / PCI, centralised or owner-of-record mode)
- Approval reminders with configurable intervals and escalation
- Approval delegation (admin-managed and portal self-service OOO)
- Auto-decline on extended inactivity (opt-in)
- Microsoft Teams approval cards (Adaptive Card via Workflows webhook)
- Email notifications with one-click approve / decline

### Runbook Engine
- Three automation strategies: Group Access, Runbook, Composite
- Visual runbook builder with drag-and-drop step ordering
- PowerShell script execution via Celery workers
- Step-by-step execution tracking with structured JSON logs
- In-app PowerShell script editor
- PowerShell module management (install from Gallery or upload custom `.zip`, with Linux compatibility flag)
- Global variables for runbooks and scripts

### Asset Lifecycle Management
- All three assignment models: capacity-pooled, dedicated-shared, assigned-personal
- Per-user quota (`max_per_user`)
- Active / inactive flag on asset definitions
- Asset statuses: Free, Reserved, Busy, Reinstall, Reinstalling, Failed, Maintenance
- Deprovision policies: access_only, return_to_pool, return_to_pool_reinstall, deallocate, delete, custom runbook
- Scheduled orders (future-dated provisioning with asset reservation)
- Automatic expiry checks and reminder emails (Celery Beat)
- Eligible requestors (restrict asset types to specific AD groups)

### Finance & Chargeback
- Cost / chargeback per asset definition (`monthly_cost`, `currency`, `cost_center`)
- Cost Report page with projected monthly spend per cost center, CSV export
- AD-driven consumer breakdown (department, cost center, company, employeeID)
- Cost-threshold alerts per cost center with email and Teams notifications
- Historical cost snapshots with `as-of` date picker
- FX conversion via configurable rate map

### Hypervisor & OS Deployment
- VMware vSphere (VM lifecycle operations via PowerCLI)
- XenServer / XCP-ng (VM lifecycle operations)

### Admin UI
- Dashboard with live pool status tiles, setup checklist, pool capacity warnings
- Full asset type configuration (categories, attributes, automation strategy)
- Asset pool management with bulk import
- Order overview and management
- Email template editor with variable placeholders (per-action templates)
- App branding (title, logo, logo position and size)
- Central settings for AD, SMTP, vSphere, XenServer, Entra ID
- Session-based login (plus `X-Admin-Key` header for API access)
- Admin RBAC — five-tier role ladder (`superadmin > admin > approver > auditor > helpdesk`)
- Per-asset-type ACL grants (scope admins to a subset of asset types)
- Separation-of-Duties enforcement (block self-approval on admin-configured types)
- Per-integration API tokens with scopes and role binding (replaces shared `X-Admin-Key`)
- API token hard-delete retention policy (opt-in)
- Admin self-service password change
- Password policy + lockout (configurable rotation days, failed-attempt lockout)

### Integrations
- Active Directory / LDAP (user validation, manager lookup, group membership)
- Microsoft Entra ID (SSO for the portal)
- SMTP (transactional email notifications)
- Microsoft Teams (approval cards and cost-threshold alerts)
- External secret management (HashiCorp Vault, CyberArk CCP/AIM, Azure Key Vault, AWS Secrets Manager, CyberArk Conjur)

### Compliance & Audit
- Append-only audit log with tamper-evident BEFORE-statement triggers
- Audit log viewer (filterable, coloured actor badges, before/after JSON diff)
- Order change log viewer
- Audit retention pruning (per-classification windows: PII / PHI / PCI)
- Field-level data classification badges (internal / PII / PHI / PCI)
- Full audit attribution (token, session, portal user, signed-link)
- Admin RBAC audit trail with role attribution

### Observability
- Prometheus `/metrics` endpoint (request rate, latency, business gauges)
- OpenTelemetry tracing (api + Celery worker, OTLP HTTP exporter)
- Grafana dashboard + Prometheus alert rules (drop-in)

### Infrastructure
- PostgreSQL database with Alembic migrations
- REST API with OpenAPI / Swagger documentation
- Docker Compose deployment
- Health probes (DB, Redis, Beat liveness, external system reachability) with email alerts
- Celery queue inspection and targeted purge
- Scheduled PostgreSQL backups with retention policy
- HA Celery Beat scheduler (celery-redbeat, Redis-backed distributed lock)

---

## Pro Edition (Commercial License)

*Includes everything in Community Edition, plus:*

### Standalone Runbooks
- Ad-hoc runbooks not tied to asset types
- Cron-scheduled runbooks with per-run history, logs, and notes

### ITSM Integration
- ServiceNow inbound HMAC-signed webhook (`POST /webhook`) for order dispatch

### OS Deployment Integration
- SCCM (task sequence triggers, device import/delete, status polling via `sccm_probe`)

### Identity Sync & HR Integration
- HR leaver webhook (`POST /hr/leaver`) — Workday, SAP SuccessFactors, MS Graph adapters
- HR leaver events viewer (`/ui/leaver-events`)
- SCIM 2.0 endpoint (`/scim/v2/*`) — leaver-focused subset of RFC 7644, drop-in for Okta / SailPoint / Ping

### Access Certification Campaigns
- Campaign creation with scope filter (asset types / cost centers / departments)
- Reviewer email + Teams card with signed-token review URL (no login required)
- Per-row Confirm / Revoke decisions (revoke triggers deprovision runbook immediately)
- Reminder emails at configurable offsets, overdue email, escalation summary
- Auto-revoke on overdue (opt-in)
- Admin drill-down at `/ui/certifications`
- Manager portal page at `/portal/certifications`

### SIEM Audit-Log Streaming
- Splunk HTTP Event Collector (HEC)
- Microsoft Sentinel — legacy Data Collector API and newer Logs Ingestion API (DCE / DCR + AAD SPN)
- Generic JSON webhook with HMAC-SHA256 body signing (`X-Hub-Signature-256`)
- Persistent cursor, automatic retry, "Send Test Event" connectivity check

---

## Edition Gating — Implementation

### License Model

```
edition = "community" | "pro"
```

Legacy aliases emitted by older signing tools (`business`, `enterprise`, `professional`) are normalised to `pro` on load.

Missing license file or any verification failure silently falls back to Community — no runtime blocking.

### Feature Availability Mechanism

Feature availability is controlled by **which code is present in the Docker image**, not by runtime license checks:

- **Community image** (`Dockerfile.community`): Pro-only files are removed at build time.
- **Pro image** (`Dockerfile.pro` / default): all files present.

Pro-only routes absent from the Community image return HTTP 404 (the router was never registered), not HTTP 403. No `try/except ImportError` gates exist in the routing layer — if the file isn't there, the endpoint simply doesn't exist.

### Pro-only files stripped from the Community image

**API routes:**
- `routes/webhook.py` — ServiceNow inbound webhook
- `routes/scim.py` — SCIM 2.0 endpoint
- `routes/hr_webhook.py` — HR leaver webhook
- `routes/admin_certifications.py` + `certifications_external.py` + `portal_certifications.py` — Access Certifications
- `routes/admin_standalone_runbooks.py` + related templates — Standalone Runbooks

**Worker workflows/modules:**
- `workflows/standalone_runner.py` — standalone runbook executor
- `workflows/sccm_probe.py` — SCCM task-sequence status polling
- `workflows/siem_streamer.py` + `modules/siem_export.py` — SIEM streaming
- `workflows/certification_notifications.py` + `certification_reminders.py` — certification Beat tasks

---

## Versioning

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-04-23 | Initial two-tier edition split (Community / Enterprise) |
| 2.0 | 2026-05-02 | Three-tier system: added Business Edition between Community and Enterprise |
| 3.0 | 2026-06-07 | Consolidated to two tiers: Community / Pro (Business and Enterprise merged into Pro) |
