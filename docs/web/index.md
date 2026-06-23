---
title: ip·Solis Documentation
slug: index
order: 0
description: Open-source IT asset lifecycle automation — self-service portal, approval workflows, runbook engine, and compliance tooling for on-premises datacenters.
---

# ip·Solis Documentation

ip·Solis is an open-source platform for IT asset lifecycle automation. It gives your users a self-service portal to request, extend, and return IT assets — VDIs, application access, infrastructure resources — while your IT team keeps full control through configurable approval workflows, PowerShell runbooks, and a tamper-evident audit trail.

Built for on-premises datacenters. Deployable in an afternoon.

---

## What's in these docs

| Section | What you'll learn |
|---|---|
| [Self-Service Portal](./self-service) | How users request assets, track orders, and manage their IT via the portal |
| [Lifecycle & Asset Pool](./lifecycle) | Assignment models, asset statuses, deprovision policies, and access certifications |
| [Automation & Runbooks](./automation) | Runbook editor, PowerShell steps, standalone runbooks, module store, global variables |
| [Compliance & Audit](./compliance) | Audit log, SIEM streaming, retention policies, data classifications |
| [FinOps & Costs](./finops) | Cost model, cost centers, FX conversion, historical snapshots, threshold alerts |
| [Integrations](./integrations) | Active Directory, Entra ID, SCIM, ServiceNow, HR webhook, vSphere, SCCM, secret backends, API tokens |
| [Security](./security) | RBAC role ladder, per-type ACLs, SoD enforcement, password policy, bearer auth |

---

## Licensing

ip·Solis is source-available under the **XenPool Commercial Source License v1.0**.

- **Free use** — purely private use by individuals outside any organizational activity, plus non-productive research/teaching at state-recognized educational institutions. No license key required.
- **Evaluation** — 30 days free in any environment.
- **Commercial use** — any productive or organizational use requires a license from XenPool GmbH, regardless of profit intent, **including public-sector bodies and non-profit / charitable organizations** (`sales@xenpool.de`).

All features ship in a single image. Features that require additional infrastructure (SCCM, ServiceNow, SCIM, etc.) are noted throughout these docs.

---

## Quick links

- [Deployment guide](../DEPLOYMENT.md) — Docker Compose setup, environment variables, first-run checklist
- [REST API](http://localhost:8000/docs) — Interactive Swagger documentation (requires a running instance)
- [GitHub repository](https://github.com/XenPool/ipsolis) — Source code, issues, and releases
