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

ip·Solis ships as a single codebase with three edition tiers gated by a runtime license:

| Edition | Summary |
|---|---|
| **Community** | AGPL-3.0, fully functional for small-to-mid-sized teams — up to 3 asset types, 100 managed users |
| **Business** | Commercial license — adds advanced workflows, standalone runbooks, PS module management, certifications, API tokens, and audit log viewer. Up to 2,000 users |
| **Enterprise** | Commercial license — adds identity sync (SCIM/HR webhook), ServiceNow, hypervisor integrations, advanced RBAC, external secret backends, HA Beat, and more. Unlimited users |

Features that require a specific edition are marked throughout these docs.

---

## Quick links

- [Deployment guide](../DEPLOYMENT.md) — Docker Compose setup, environment variables, first-run checklist
- [REST API](http://localhost:8000/docs) — Interactive Swagger documentation (requires a running instance)
- [GitHub repository](https://github.com/XenPool/ipSolis) — Source code, issues, and releases
