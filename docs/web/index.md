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

## Editions

ip·Solis ships in two editions. The split is built into the Docker image — the Community image simply omits the Pro-only files (absent endpoints return HTTP 404, not 403); there is no runtime feature-gating by license key.

| Edition | Summary |
|---|---|
| **Community** | AGPL-3.0, free and fully functional for on-premises IT asset lifecycle management. Self-service portal, approval workflows, asset-type runbooks, PowerShell module store, cost reporting, audit log + viewer + retention, API tokens, RBAC, and Active Directory / Entra ID / SMTP / Teams / vSphere / XenServer integrations. |
| **Pro** | Commercial license — adds the operational integrations stripped from the Community image: standalone (ad-hoc & cron) runbooks, access certification campaigns, SCIM 2.0, HR leaver webhook, ServiceNow inbound webhook, SCCM, and SIEM audit-log streaming. |

Pro-only features are marked *(Pro)* throughout these docs.

---

## Quick links

- [Deployment guide](../DEPLOYMENT.md) — Docker Compose setup, environment variables, first-run checklist
- [REST API](http://localhost:8000/docs) — Interactive Swagger documentation (requires a running instance)
- [GitHub repository](https://github.com/XenPool/ipsolis) — Source code, issues, and releases
