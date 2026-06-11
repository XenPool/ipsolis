---
title: Integrations
slug: integrations
order: 6
description: Active Directory, Microsoft Entra ID, SCIM 2.0, ServiceNow webhook, HR leaver webhook, VMware vSphere, XenServer/XCP-ng, SCCM, SMTP, external secret backends, and API tokens.
---

# Integrations

ip·Solis connects to your existing infrastructure rather than replacing it. All integration credentials are configured at runtime via **Admin → Settings** and stored in the `app_config` table — no container rebuild required when credentials change.

![Settings integrations view](./screenshots/admin-integrations-settings.png)

---

## Active Directory / LDAP


Active Directory is the backbone of user identity in ip·Solis. It is used for:

- **User validation** — confirming that a requester's account exists and is active
- **Manager lookup** — resolving the requester's manager for approval routing
- **Group membership** — adding and removing users from groups as part of runbook steps and Group Access automation
- **Eligible requestor checks** — verifying that a user is a member of a restricted AD group before allowing a request

Configure in **Admin → Settings → Active Directory**:

| Setting | Description |
|---|---|
| Server | LDAP server hostname or IP |
| Port | Default: 389 (LDAP) or 636 (LDAPS) |
| Bind DN / password | Service account credentials |
| Base DN | Search root for user and group lookups |
| Auth type | NTLM or Kerberos (NTLM signing supported) |
| Consumer attributes | AD field names for `department`, `cost_center`, `company`, `employeeID`, `title` |

---

## Microsoft Entra ID (Azure AD) SSO


Entra ID provides SSO authentication for the self-service portal. When `entra.mode` is set to `entra_only` or `entra_with_onprem`, users are redirected to Microsoft's login page and returned to the portal with a verified identity.

Configure in **Admin → Settings → Entra ID**:

| Setting | Description |
|---|---|
| Tenant ID | Your Azure AD tenant |
| Client ID | App registration client ID |
| Client secret | App registration secret |
| Redirect URI | Must match the registered redirect in Azure Portal |
| Domain allow-list | Optional: restrict login to specific email domains |
| Mode | `disabled` / `entra_only` / `entra_with_onprem` |

Use **Test Entra Credentials** to verify the client credentials via a token-flow check before saving.

---

## SCIM 2.0 *(Pro)*

ip·Solis exposes a leaver-focused SCIM 2.0 endpoint at `/scim/v2/*` for identity providers that support SCIM deprovisioning. Compatible with Okta, SailPoint, and Ping.

The supported operations that trigger the leaver flow are:

- `DELETE /scim/v2/Users/{id}` — triggers full leaver processing
- `PATCH /scim/v2/Users/{id}` with `active=false` — triggers full leaver processing
- `PUT /scim/v2/Users/{id}` with `active=false` — triggers full leaver processing

Create, read, and update operations are acknowledged but no-op (ip·Solis does not store user accounts — users become real when they make their first order).

**Authentication**: mint a token with `scim:read` + `scim:write` scopes from **Admin → API Tokens** and paste it into your IDP connector configuration.

See [Lifecycle & Asset Pool → HR Leaver Flow](./lifecycle#hr-leaver-flow) for the full leaver behaviour.

---

## HR Leaver Webhook *(Pro)*

A purpose-built webhook at `POST /hr/leaver` for HR systems that push termination events. Supported natively for Workday, SAP SuccessFactors, Microsoft Graph, and a generic ip·Solis-native format.

**Authentication**: scoped API token (scope `hr:leaver`) or HMAC-SHA256 body signing using `WEBHOOK_SECRET_TOKEN`.

See [Lifecycle & Asset Pool → HR Leaver Flow](./lifecycle#hr-leaver-flow) for payload formats and full documentation.

---

## ServiceNow Webhook *(Pro)*

ip·Solis can receive order dispatch requests from ServiceNow via an HMAC-signed inbound webhook at `POST /webhook/servicenow`.

Configure the shared secret in **Admin → Settings → ServiceNow** (`WEBHOOK_SECRET_TOKEN`). The webhook validates the `X-Hub-Signature-256` header on every request.

The webhook payload maps directly to an order creation request. ServiceNow-originated orders go through the same approval workflows, runbooks, and audit trail as portal orders.

---

## VMware vSphere

vSphere VM lifecycle operations are executed via PowerCLI scripts stored in the script module store (category: `vmware`). The worker container runs `pwsh` (PowerShell 7 on Linux) with SSL certificate bypass pre-configured for self-signed vCenter certificates.

Configure in **Admin → Settings → VMware vSphere**:

| Setting | Description |
|---|---|
| vCenter server | Hostname or IP |
| Username / password | Service account with VM management permissions |

vSphere operations (power on/off, clone, delete, reconfigure) are implemented as script modules that are called from runbook steps. Add these scripts to your asset type runbooks under **Admin → Asset Definitions → Runbooks**.

---

## XenServer / XCP-ng

XenServer and XCP-ng VM lifecycle operations follow the same pattern as vSphere — PowerShell scripts stored as script modules (category: `xenserver`) and executed via `pwsh` in the worker container.

Configure in **Admin → Settings → XenServer/XCP-ng**:

| Setting | Description |
|---|---|
| XenServer host | Pool master hostname or IP |
| Username / password | XenAPI credentials |

SSL certificate prompts are auto-answered via stdin injection so scripts don't hang on untrusted certificates.

---

## SCCM *(Pro)*

SCCM integration enables automated OS deployment workflows:

- **Task sequence triggers** — kick off an SCCM task sequence for a specific device via WinRM
- **Device import** — add a computer record to SCCM via the AdminService REST API (Kerberos auth)
- **Device delete** — remove a computer record after decommissioning
- **Status polling** — the `sccm_probe` Celery workflow polls SCCM for task sequence completion status and advances the order state accordingly

Configure in **Admin → Settings → SCCM**:

| Setting | Description |
|---|---|
| SCCM server | Site server hostname |
| WinRM endpoint | WinRM connection string |
| AdminService URL | `https://<server>/AdminService/v1.0` |
| Kerberos principal | Service account UPN |
| Kerberos password | Service account password |

---

## SMTP


All transactional email (approval notifications, reminders, expiry warnings, leaver notifications, health alerts) is sent via Python's `smtplib`.

Configure in **Admin → Settings → SMTP**:

| Setting | Description |
|---|---|
| Host / port | SMTP server address and port |
| Username / password | SMTP authentication credentials |
| From address | Sender address shown in emails |
| TLS mode | STARTTLS or SSL/TLS |
| Reply-to | Optional reply-to address for approval emails |

Use **Send Test Email** to verify the connection before saving.

---

## External Secret Backends

Replace plaintext credentials in `app_config` with references to an external secret manager. ip·Solis resolves references at read time with a 60-second process-local TTL cache.

Supported backends:

| Backend | Reference format |
|---|---|
| HashiCorp Vault | `vault://<path>[#<field>]` |
| CyberArk CCP/AIM | `ccp://[<safe>/]<object>` |
| Azure Key Vault | `azurekv://<vault>/<secret>` |
| AWS Secrets Manager | `awssm://<secret-id>[#<field>]` |
| CyberArk Conjur | `conjur://<identifier>[#<field>]` |

Plain string values continue to work unchanged, so you can migrate one credential at a time.

**Vault auth**: static token, AppRole (role_id + secret_id), or Kubernetes JWT.

**Azure KV auth**: Azure AD service principal (independent of the Entra ID SSO config).

**AWS auth**: static IAM keys or native `sts:AssumeRole` with automatic session refresh.

**Bulk migration tool**: **Settings → Compliance → External Secret Backend → Migrate plaintext secrets to backend** walks all `is_secret=true` rows, pushes plaintext values to the active backend, and replaces them with references. Includes a dry-run preview and per-row report.

---

## API Tokens

Per-integration named API tokens replace the single shared `X-Admin-Key` with individually revocable, expiring, scoped bearer tokens.

![API tokens page](./screenshots/admin-api-tokens.png)

Tokens are stored as SHA-256 hashes. The raw token (`xpat_…`) is shown once on creation and cannot be recovered — treat it like a password.

### Scopes

| Scope | Access |
|---|---|
| `admin:*` | Full admin API access |
| `admin:read` | Read-only admin access |
| `orders:write` | Create orders via the REST API |
| `webhook:servicenow` | Call the ServiceNow inbound webhook |
| `hr:leaver` | Call the HR leaver webhook |
| `scim:read` | SCIM GET operations |
| `scim:write` | SCIM POST/PUT/PATCH/DELETE (triggers leaver flow) |

### Role Binding

Tokens may be issued with a specific role (`superadmin`, `admin`, `approver`, `auditor`, `helpdesk`). Role-gated routes enforce both scope and role. A creator can only issue tokens at or below their own role — no privilege escalation.

### Hard-Delete Retention

An opt-in daily task (`api-token-purge-daily`) hard-deletes tokens whose `revoked_at` or `expires_at` is older than `api_tokens.purge_after_days`. Default is `0` (retain forever). Each hard-delete produces an audit row.

### Legacy `X-Admin-Key`

The original `X-Admin-Key` header continues to work as a virtual superadmin credential, so existing integrations don't break on upgrade. It is recommended to migrate to named API tokens for new integrations.
