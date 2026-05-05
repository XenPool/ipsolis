---
title: Self-Service Portal
slug: self-service
order: 1
description: How end users request IT assets, track their orders, manage active resources, and use deputy ordering — all without an IT ticket.
---

# Self-Service Portal

The self-service portal lets employees request IT assets, track order status, extend or return active resources, and manage their digital workspace — without raising a helpdesk ticket. The portal is accessible at `/portal` and is fully separated from the admin UI.

![Portal catalog](./screenshots/portal-catalog.png)

---

## Authentication

The portal supports three authentication modes, configured under **Admin → Settings → Entra ID**:

| Mode | Behaviour |
|---|---|
| `disabled` | Portal is open; all users share an anonymous identity. Suitable for testing or internal-only deployments without SSO |
| `entra_only` | Entra ID (Azure AD) SSO required. Users sign in with their Microsoft 365 account |
| `entra_with_onprem` | Entra ID SSO plus an on-premises LDAP membership check. The user must be both authenticated in Entra and present in the configured AD group |

When SSO is enabled, the user's email address is resolved automatically. Manager lookup for approval routing uses the same AD connection.

---

## Requesting an Asset

### Browsing the Catalog

The catalog (`/portal/orders/new`) shows all active asset definitions the logged-in user is eligible to request. Each card shows the asset name, description, category, and — where configured — the projected monthly cost.

![New order form](./screenshots/portal-order-new.png)

**Search and filter** appear automatically for catalogs with more than six definitions. The search matches name, description, and help text; the category dropdown filters by asset type category. Both work client-side with no page reload.

**Help text** — admins can attach a markdown-formatted description to each asset definition. When a requester selects a type, the rendered help text appears above the attribute fields. Use this to document pre-installed software, eligibility requirements, expected provision time, and support contacts.

### Eligible Requestors *(Business)*

Asset types can be restricted to specific Active Directory groups. Users who are not members of the configured group do not see the definition in the catalog.

### Filling in the Order Form

After selecting an asset type, the requester fills in any user-supplied attributes (e.g., hostname prefix, purpose, duration). Fields tagged with a **data classification** (`PII`, `PHI`, or `PCI`) show a warning badge so requesters are aware of the sensitivity before submitting.

### Per-User Quota

If the asset type has a `max_per_user` limit set, the portal returns an error if the user already holds that many active instances of that type. The check covers all non-terminal states (pending, processing, provisioned, etc.) so users cannot bypass the limit with stacked future-dated orders.

### Per-Order Cost Projection *(Business)*

When an asset type has a `monthly_cost` configured, the order form shows the projected total (`monthly_cost × months_requested`) before the user submits. This appears in the **Access & Duration** card.

---

## Approval Flow

Orders that require approval enter a `pending_approval` state. The portal displays the current approval status on the order detail page.

Approvers receive an email with one-click **Approve** and **Decline** links. No portal login is required for the approver — the link contains a signed token that works from any email client.

**Microsoft Teams cards** *(Business)* — when Teams integration is enabled, the same approve/decline prompt is also delivered as an Adaptive Card to the configured Teams channel.

**Reminders** *(Business)* — if an approver hasn't responded after the configured interval (default: 24 hours), the system re-sends the notification. Up to three reminders are sent before escalation.

**Escalation** *(Business)* — after reminders are exhausted, the configured escalation contacts are notified. In assignment mode, they receive their own one-click approval link.

**Auto-decline** *(Business)* — stale pending approvals past the configured inactivity window are automatically declined by a daily background task.

---

## My IT Dashboard

The **My IT** view (`/portal/my-it`) shows all active assets assigned to the logged-in user.

![My IT view](./screenshots/portal-my-it.png)

From here, users can:

- **Extend** — submit a request to extend the expiry date of an active asset (subject to approval if configured)
- **Modify** — change user-supplied attributes on an existing order (subject to re-approval if the asset type has `reapproval_on_modify` enabled)
- **Return** — trigger deprovisioning and release the asset back to the pool
- **Cancel** — cancel a pending or scheduled order before it is processed

---

## Deputy Ordering *(Business)*

Deputy ordering lets one user place an order on behalf of another. The requester selects the target user in the order form. The resulting order is attributed to the beneficiary, not the submitting user, so AD group membership, approvals, and audit rows all reference the correct person.

Use cases: IT admin ordering a VDI for a new hire before their first day; a manager ordering on behalf of a team member who cannot access the portal.

---

## Scheduled Orders *(Business)*

Orders can be dated in the future. A scheduled order reserves the asset immediately (so no one else can claim it) but does not trigger provisioning until the scheduled start date arrives. The Celery Beat task `check-scheduled-orders` runs hourly to dispatch ready orders.

Scheduled orders appear in My IT with a `scheduled` status badge and the target start date.

---

## Approval Delegation

**Admin-configured delegation** — an administrator can configure a deputy window for any approver (e.g., "Stefan is on leave 1–15 August; route his approvals to Jupp"). New orders during the window automatically address the deputy. The original assignee is captured in the audit trail.

**Self-service delegation** *(Business)* — managers can configure their own delegation windows directly from the portal at `/portal/delegations`, without going through an admin. The server enforces that a user can only configure delegation for their own approvals.

---

## Access Certifications *(Business)*

When an access certification campaign is active and the logged-in user is a reviewer, a notification appears in the portal directing them to `/portal/certifications`. This page shows all pending review rows assigned to the user, with one-click **Confirm** (user keeps access) or **Revoke** (access is pulled immediately) for each.

---

## Leaver Blocking

If a user is flagged as a leaver (via HR webhook or SCIM), they are blocked from placing new orders immediately. The portal shows a clear message explaining that their account has been flagged and directing them to contact IT if this is in error.

---

## Multi-Language Support

The portal UI is available in **English, German, French, Spanish, and Italian**. The active locale is detected from the browser's `Accept-Language` header and can be overridden via a language selector. All labels, validation messages, email templates, and empty states are localized.
