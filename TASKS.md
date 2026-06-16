# ip·Solis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.

---

## Open Tasks

### [open] Publish prebuilt Docker images to GHCR (ghcr.io) via CI

**Problem:** ip·Solis ships only as source — installs run `docker compose up --build`,
which compiles locally (slow first run, no version pinning) and gives **no visibility
into adoption**. GitHub git-clone counts are inflated by our own CI (the `ipsolis-web`
build pulls docs from this repo on every deploy) and never reveal *who* cloned, so they
are a poor proxy for real installations.

**Proposed solution:** Publish prebuilt images to **GitHub Container Registry**
(`ghcr.io/xenpool/ipsolis-*`) from a GitHub Actions workflow on release tags
(`v*.*.*`), and add a compose overlay that **pulls** the published image instead of
building. GHCR exposes **per-package pull counts** → the closest privacy-respecting
signal for actual downloads/installations.

**Why this approach:**
- Pull counts beat clone counts as an install proxy (clones polluted by CI; GitHub hides cloner identity by design).
- Faster, reproducible installs (no local build); version-pinned images.
- `ghcr.io` is free for public images, uses `GITHUB_TOKEN`, no extra registry account.
- Reuses the existing Dockerfile(s) (Community / Pro tiers).

**Implementation slices:**
- [ ] GitHub Actions workflow: build + push multi-arch images to `ghcr.io` on `v*.*.*` tags (+ `:latest`) via `docker/build-push-action` (`packages: write`, `GITHUB_TOKEN`)
- [ ] Tag both tier images if applicable (community / pro Dockerfiles)
- [ ] Make the GHCR package **public**; document image usage in README + `docs/DEPLOYMENT.md`
- [ ] Add `docker-compose.ghcr.yml` (or adjust `docker-compose.prelive.yml`) to pull `ghcr.io/xenpool/ipsolis-api:<tag>` instead of `build:`
- [ ] (optional) Document where to read pull counts (package page / API) for adoption tracking

**Related (deeper signal, separate task):** an opt-in, anonymous update-checker
phone-home would be the only way to count *actually running* deployments (GHCR pulls
still ≠ live installs). Commercial installs are already known via license activation.

---

---


### [open] Cloud group management via Microsoft Graph — future
Extend `target_executor` to manage Entra cloud-only security groups for asset types
that define `{"type": "entra_group", "group_id": "..."}` targets. Requires
Microsoft Graph API integration (separate sprint).

---

### [open] Okta as 2nd Identity Provider — future
Add Okta as an optional second IDP alongside Entra ID for portal SSO. Estimated effort: 4–6 days.

**Context:** Okta uses standard OIDC (same protocol as Entra underneath MSAL), so no exotic
library is needed. The main work is abstracting the auth layer away from Entra-specific assumptions.

**Key design decisions to resolve before starting:**
- [ ] IDP routing strategy: domain-based auto-routing vs. a picker page at `/portal/login/select`

**Implementation slices:**
- [ ] Extract generic OIDC helper (`api/app/utils/oidc.py`)
- [ ] New `okta.*` app_config keys + DB migration
- [ ] Auth routing: IDP selection logic, second callback endpoint `/portal/auth/okta/callback`
- [ ] Okta logout support
- [ ] Admin UI: Okta settings section + "Test connection" button
- [ ] Make portal auth gate provider-agnostic (currently hardcoded to `entra.mode` in `portal.py`)

---

## Done — Summary

All items below are shipped. Detailed implementation notes live in git history.

| Area | Shipped | Notes |
|------|---------|-------|
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
