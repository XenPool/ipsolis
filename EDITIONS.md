# ip·Solis – Edition Feature Matrix

This document defines the feature split across the three editions of ip·Solis:
**Community** (open-source, AGPL-3.0), **Business** (commercial), and **Enterprise** (commercial).
It is the canonical reference for edition gating throughout the codebase.

## Guiding Principles

- The Community Edition must be **fully functional** for small-to-mid-sized teams — not a crippled demo.
- Business features target organizations with **operational automation, compliance basics, and custom integrations**.
- Enterprise features target organizations with **identity sync, ITSM integration, hypervisor lifecycle management, and regulated compliance requirements**.
- Edition gating is implemented via **runtime license checks and feature flags**, not separate codebases or branches.
- All features ship in a **single codebase**. Business and Enterprise features are present but gated.

---

## Community Edition (AGPL-3.0)

### Self-Service Portal
- Asset request, status tracking, extend, return
- "My IT" dashboard (active assets overview)
- Multi-language UI (EN, DE, FR, ES, IT)
- Entra ID (Azure AD) single sign-on
- Up to 3 asset types · up to 100 managed users

### Approval Workflows
- Manager approval (auto-resolved from Active Directory)
- Email notifications with one-click approve / decline

### Runbook Engine
- Three automation strategies: Group Access, Runbook, Composite
- Runbook definition with ordered steps (list-based configuration)
- PowerShell script execution via Celery workers
- Step-by-step execution tracking with structured JSON logs
- In-app PowerShell script editor

### Asset Lifecycle Management
- All three assignment models: capacity-pooled, dedicated-shared, assigned-personal
- Asset statuses: Free, Reserved, Busy, Reinstall, Reinstalling, Failed, Maintenance
- Standard deprovision policies: access_only, return_to_pool, return_to_pool_reinstall, deallocate, delete
- Automatic expiry checks and reminder emails (Celery Beat)

### Admin UI
- Asset type configuration (categories, attributes, automation strategy)
- Asset pool management
- Order overview and management
- Settings for AD, SMTP, Entra ID
- Dashboard with live pool status tiles

### Integrations
- Active Directory / LDAP (user validation, manager lookup, group membership)
- SMTP (transactional email notifications)

### Infrastructure
- PostgreSQL database with Alembic migrations
- REST API with OpenAPI / Swagger documentation
- Docker Compose deployment
- Basic health probes (DB, Redis connectivity)
- Append-only audit log (data written in all editions)

---

## Business Edition (Commercial License)

*Includes everything in Community Edition, plus:*

### Advanced Workflows
- Application owner approval (second approval tier)
- Re-approval on asset modification (configurable per asset type)
- Deputy support (order on behalf of another user)
- Scheduled orders (future-dated provisioning with asset reservation)
- Eligible requestors (restrict asset types to specific AD groups)
- Custom runbook deprovision policy

### Visual Runbook Builder
- Drag-and-drop step ordering
- Visual workflow composition

### Standalone Runbooks
- Ad-hoc runbooks (not tied to asset types)
- Cron-scheduled runbooks with per-run history, logs, and notes

### PowerShell Module Management
- Install modules from PowerShell Gallery
- Upload custom modules (.zip)
- Module registry with metadata

### Customization
- Email template editor with variable placeholders (per-action templates)
- App branding (title, logo, logo position and size)
- Global variables for runbooks and scripts

### Audit & Compliance
- Audit log viewer (UI)
- Order change log viewer (UI)
- Access certification campaigns (ISO 27001 / SOX / PCI)

### API & Integration
- Per-integration named API tokens with scopes and role binding

### Limits
- Up to 2,000 managed users · unlimited asset type definitions

---

## Enterprise Edition (Commercial License)

*Includes everything in Business Edition, plus:*

### Identity Sync & HR Integration
- SCIM 2.0 deprovisioning (Okta, Ping, SailPoint)
- HR leaver webhook (Workday, SAP, custom HR systems)
- HR leaver events viewer

### ITSM Integration
- ServiceNow inbound HMAC-signed webhook for order dispatch

### Hypervisor & OS Deployment
- VMware vSphere (VM lifecycle operations via PowerCLI)
- XenServer / XCP-ng (VM lifecycle operations)
- SCCM (task sequence triggers, device import/delete, status polling)

### Advanced Maintenance & Operations
- Scheduled PostgreSQL backups with configurable retention
- Manual backup / restore / download via Admin UI
- Celery queue inspection and targeted purge
- Email alerts on health state transitions
- Audit log retention policies (PII / PHI / PCI classification)

### Advanced Access Control
- Per-asset-type ACL grants (restrict asset-type management to specific admin groups)
- Role-bound API tokens (issue tokens locked to a specific admin role)
- Separation-of-Duties enforcement (block self-approval and cross-role conflicts)
- Password rotation and lockout policy

### Limits & Deployment
- Unlimited users and asset type definitions
- On-premises / air-gapped deployment support
- Dedicated solution architect
- Custom SLA and security review

---

## Edition Gating – Implementation Guide

### License Model

```python
# License check pseudocode
EDITION = load_license()  # "community" | "business" | "enterprise"
```

### Feature Key Registry

Feature keys are split into two frozensets in `api/app/utils/license.py`:

```python
BUSINESS_FEATURE_KEYS: frozenset[str] = frozenset({
    "standalone_runbooks",    "visual_runbook_builder", "ps_module_management",
    "deputy_support",         "scheduled_orders",       "app_owner_approval",
    "reapproval_on_modify",   "email_template_editor",  "app_branding",
    "eligible_requestors",    "global_variables",        "audit_log_viewer",
    "change_log_viewer",      "api_token_management",   "certifications",
})

ENTERPRISE_ONLY_FEATURE_KEYS: frozenset[str] = frozenset({
    "servicenow_webhook",     "hr_webhook",             "hr_leaver_events",
    "scim",                   "vsphere_integration",    "xenserver_integration",
    "sccm_integration",       "audit_retention",        "advanced_maintenance",
    "custom_deprovision",     "rbac_asset_type_grants", "rbac_token_role_binding",
    "rbac_sod_enforcement",   "password_policy",
})
```

### Gating Hierarchy

- **Enterprise license** → all features enabled (backward-compatible with `features: ["all"]`)
- **Business license** → `BUSINESS_FEATURE_KEYS` enabled, `ENTERPRISE_ONLY_FEATURE_KEYS` blocked
- **Community (no license / invalid)** → all gated features disabled

### Gating Pattern

Features are gated at three levels:

1. **UI layer** — Menu items and pages are conditionally rendered:
   ```jinja2
   {% if is_business %}  {# Business-tier nav item #}
     <a href="/ui/standalone-runbooks">Standalone Runbooks</a>
   {% endif %}
   {% if is_enterprise %}  {# Enterprise-only nav item #}
     <a href="/ui/leaver-events">Leaver Events</a>
   {% endif %}
   ```

2. **API layer** — Endpoints return `HTTP 403` with an upgrade message:
   ```python
   # Business-tier endpoint
   dependencies=[require_business("standalone_runbooks")]

   # Enterprise-only endpoint
   dependencies=[require_enterprise("scim")]
   ```

3. **Worker layer** — Tasks check edition before execution:
   ```python
   if not is_feature_enabled("servicenow_webhook"):
       return {"status": "skipped", "reason": "enterprise_only"}
   ```

---

## Versioning

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-04-23 | Initial two-tier edition split (Community / Enterprise) |
| 2.0 | 2026-05-02 | Three-tier system: added Business Edition between Community and Enterprise |
