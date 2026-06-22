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
- [x] GitHub Actions workflow: build + push images to `ghcr.io` on `v*.*.*` tags (+ `:latest`)
      via `docker/build-push-action` — `release.yml` (api + worker, VERSION-match guard,
      GitHub Release, web dispatch). ⚠️ currently **single-arch `linux/amd64`** — multi-arch
      (arm64) still open.
- [ ] ~~Tag both tier images (community / pro)~~ — N/A: tiers consolidated to one
      `api/Dockerfile` + one `worker/Dockerfile`. Revisit only if tiers are re-split.
- [x] Make the GHCR package **public** — done (2026-06-21): `ghcr.io/xenpool/ipsolis-{api,worker}`
      now return **HTTP 200** to anonymous pulls, so `docker-compose.ghcr.yml` works without
      `docker login`. Image usage referenced in `docs/DEPLOYMENT.md` + `INSTALL.md`.
- [x] Add `docker-compose.ghcr.yml` to pull `ghcr.io/xenpool/ipsolis-{api,worker}:<tag>`
      instead of `build:` — self-contained file, pinnable via `IPSOLIS_VERSION` (default
      `:latest`); layer `docker-compose.prelive.yml` for nginx/TLS.
      ⚠️ Images bake only app code + alembic, **not** `locales/`/`scripts/`, so those are
      still bind-mounted → consider baking them in the Dockerfiles for clone-free installs.
- [ ] (optional) Document where to read pull counts (package page / API) for adoption tracking

**Still open here:** multi-arch (arm64) build; bake `locales/`+`scripts/` into images for
truly clone-free installs; pull-count tracking note. (GHCR public flip ✅ done.)

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

### [open] Provider-agnostic SSO (generic OIDC) — Entra + any compliant IdP
De-couple portal SSO from Entra-specific assumptions and support any standards-
compliant OIDC IdP (Okta, Ping, Google, Keycloak, Authentik, Zitadel) via a single
generic code path. "Okta support" then = a config entry, not a vendor integration.
Estimated effort: ~4 days (no backward-compat burden — no customers yet).

**Context:** All target IdPs speak standard OIDC. Using the IdP's discovery document
(`.well-known/openid-configuration`) lets each provider self-configure from just an
issuer URL + client credentials — no per-vendor code. Scope is OIDC-first; SAML 2.0
is a separate future task (relevant mainly as an enterprise-procurement checkbox).
No existing deployments → design the config schema correctly from the start, no
`entra.*` legacy to carry, no migration path required.

**⚠️ Process note (agent handoff):** Resolve the open design decisions below BEFORE
starting any implementation slice. Do not let the agent jump straight into `oidc.py`.

**Design decisions:**
- [x] Entra handling: FOLD INTO the generic provider model — single code path, no
      special case. (Decided: no backward-compat reason to keep a parallel MSAL path.)
      → MSAL audit (2026-06-20): only standard OIDC calls in use (authorize URL, code
        exchange, client-credentials test, manual logout URL). No token cache / OBO /
        Graph / device-code / cert auth. Nothing MSAL-only is dropped. Only Entra-specific
        nuance to preserve: `preferred_username`→UPN claim mapping → becomes a per-provider
        claim-mapping config.
- [x] IDP routing strategy: **picker at `/portal/login`, auto-skipped when exactly one
      provider is enabled** (single-IdP UX unchanged). Domain-based home-realm routing is a
      later optional per-provider enhancement, not built now.
- [x] Config shape: **provider registry `idp.<id>.*`** in `app_config` (N providers, stable
      ids, matches parametric callback). Portal auth gate moves from `entra.mode` to
      `portal.auth_required` + per-provider `idp.<id>.enabled`.
- [x] SAML 2.0: **out of scope** — OIDC-first. All target IdPs speak OIDC. SAML recorded as
      a separate future task (enterprise-procurement checkbox, no current technical need).

**Implementation slices:** _(code-complete 2026-06-20 — pending operator local test/build)_
- [x] Generic OIDC helper consuming discovery doc (`api/app/utils/oidc.py`) — JWKS
      ID-token signature validation + nonce via PyJWT[crypto] (added to requirements)
- [x] Provider-registry config schema (`idp.<id>.*` in `app_config`) + `portal.auth_required`
      gate + `auth.ldap_enabled`
- [x] Parametric callback endpoint `/portal/auth/{provider_id}/callback`
- [x] Generic RP-initiated logout via provider `end_session_endpoint`
- [x] Admin UI: add/edit/delete OIDC provider + "Test connection" (discovery probe);
      endpoints `GET /admin/config/oidc/providers`, `PUT/DELETE /admin/config/oidc/{id}`,
      `POST /admin/config/oidc/{id}/test`, `PUT /admin/portal-auth`
- [x] Portal auth gate made provider-agnostic (`portal.py` → `oidc.auth_required`);
      login picker auto-skips to the single enabled method
- [x] Retired `entra.py` + `entra.*` keys; health probe `entra`→`sso`; setup checklist updated

**Documentation updates:**
- [x] SSO setup guide rewritten provider-agnostic with Entra + Okta recipe blocks (`docs/DEPLOYMENT.md` §8)
- [x] Parametric callback documented (redirect-URI guidance in §8; auto-exposed in Swagger)
- [x] README / datasheet / CLAUDE.md lines updated to "SSO via OIDC — Entra ID, Okta, …"
- [x] Admin UI in-app help text for the new provider settings section
- [x] CHANGELOG `[Unreleased]` entry (Added/Changed/Removed); 5-locale i18n keys added

**Out of scope (separate future tasks):**
- SAML 2.0 SSO — split out of this task on 2026-06-20 (OIDC-first decision). Enterprise-
  procurement checkbox; no current target IdP requires it. Needs metadata exchange +
  signed-assertion validation (`python3-saml`), a different code path from OIDC.
- Okta-specific OIN listing / certified app — defer until a paying enterprise requires it
- SCIM provisioning (joiner/mover/leaver → asset lifecycle) — higher strategic value, own task

------

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
