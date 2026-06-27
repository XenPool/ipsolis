# ip·Solis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.

---

## Open Tasks

### [open] SCIM provisioning (joiner/mover/leaver → asset lifecycle)

Extend SCIM 2.0 beyond the current **leaver-focused** subset (already shipped) to full
**joiner/mover/leaver** provisioning that drives the asset lifecycle — a drop-in target for
Okta / SailPoint / Ping provisioning workflows. Higher strategic value than SAML. Split out of
the provider-agnostic SSO task on 2026-06-24.

**Scope / related:**
- Builds on the existing `/scim/v2/*` endpoint (ServiceProviderConfig, ResourceTypes, Schemas,
  Users CRUD; DELETE / PATCH `active=false` already trigger the leaver flow).
- Pulls in the deferred "HR feed + SCIM slice 2": full SCIM filter grammar, `/Groups` shim,
  bulk operations.
- Joiner/mover → asset lifecycle mapping is the new design work (how SCIM create/update maps to
  asset orders / access grants).

---

### [open] Cloud group management via Microsoft Graph — future
Extend `target_executor` to manage Entra cloud-only security groups for asset types
that define `{"type": "entra_group", "group_id": "..."}` targets. Requires
Microsoft Graph API integration (separate sprint).

---

## Done — Summary

All items below are shipped. Detailed implementation notes live in git history.

| Area | Shipped | Notes |
|------|---------|-------|
| Provider-agnostic SSO (generic OIDC) | 2026-06-20 | Any compliant IdP via discovery doc (Entra, Okta, Ping, Google, Keycloak…); provider registry `idp.<id>.*`, parametric callback `/portal/auth/{id}/callback`, login picker, RP-initiated logout; retired `entra.py`/MSAL path; on-prem LDAP alongside. **Deferred:** SAML 2.0 + Okta OIN listing; SCIM provisioning is its own open task |
| GHCR prebuilt images | 2026-06 | CI build+push to ghcr.io on `v*.*.*` tags, public packages, `docker-compose.ghcr.yml`, `locales/`+`scripts/` baked into images, pull-count note (`docs/internal/metrics.md`); multi-arch arm64 intentionally skipped (amd64-only — on-prem is amd64) |
| Admin RBAC (slices 1–4) | 2026-04-26/27 | Per-user accounts, 5-tier role ladder, ACLs, SoD, lockout, password rotation |
| External secret management | 2026-04-26/30 | Vault, CCP, Azure KV, AWS SM, Conjur; AppRole/JWT; AssumeRole; migration tool |
| API tokens with scopes | 2026-04-26 | Bearer auth, 14-scope catalog, ServiceNow webhook, hard-delete purge |
| Audit log + SIEM | 2026-04-26/30 | Splunk HEC, Sentinel Logs Ingestion API, streaming-failure alerts |
| Conditional approval rules | 2026-04-26/30 | AND/OR/NOT visual editor, per-bucket supersession, escalation v2 |
| Per-classification approval routing | 2026-04-30 | Compliance officer + owner-of-record modes for PII/PHI/PCI |
| Access certification campaigns | 2026-04-30 | Slice 1+2: schema, admin CRUD, signed-token review URL, auto-revoke Beat task |
| Multi-instance HA | 2026-04-30 | Multi-replica API/worker docs, Postgres standby + failover docs, Beat-alive health probe |
| Portal SSO (Entra ID) | 2026-03-23 | MSAL, session middleware, `entra.mode` config, domain check |
| Portal auth `onprem_ldap` mode | 2026-06 | LDAP bind form, 5-locale i18n, `entra_with_onprem` removed (not implemented, no prod deployments) |
| Open Core model | 2026-04-xx | Community + Pro tiers, two Dockerfiles, license simplification, public mirror repo |
| PS Modules | 2026-04-30 | Linux compatibility flag, upload support, Gallery installer |
| Standalone Runbooks | — | Ad-hoc + cron-scheduled runbooks, execution history |
| Leaver Events / Certifications | — | Bulk lifecycle triggers, access certification campaigns |
| Cost reporting | — | Chargeback breakdown, cost threshold alerts |
| Deployment docs | 2026-06 | Full EN + DE deployment guide, sudo fixes, nginx template, docker-compose.prod.yml |
| Production overlay rename | 2026-06-25 | `docker-compose.prelive.yml` → `docker-compose.prod.yml` (hard rename, no alias). Updated CI (`deploy-prelive.yml`), `docker-compose.ghcr.yml` comments, `bootstrap-certs.sh`, README, DEPLOYMENT.md/.de.md (dropped "historical name" comments). Prelive *environment*/workflow names unchanged. **Action:** update LinPre1/LinPre3 `COMPOSE_FILE` env to the new filename |
