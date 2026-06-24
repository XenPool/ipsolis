# ip·Solis – Task Backlog

Format: `[open]` / `[done]` / `[blocked]`. Add new tasks at the top of their section.

---

## Open Tasks

### [open] Rename `docker-compose.prelive.yml` → `docker-compose.prod.yml` (production overlay naming)

**Problem:** The non-dev compose overlay is named `docker-compose.prelive.yml`, but it is in
fact the **production** overlay (adds nginx/TLS termination, removes the dev bind-mounts).
Operators doing a production install/update see `prelive` in the `COMPOSE_FILE` export and
reasonably worry they picked the wrong file. The name is historical (introduced first for the
prelive environment) and now misleads the production audience. The Done-summary table below even
refers to it as `docker-compose.prod.yml`, which it never actually was.

**Interim mitigation (done 2026-06-24):** A clarifying comment was added above the
`export COMPOSE_FILE=…` lines in `docs/DEPLOYMENT.md` / `.de.md` §6 + §11
("docker-compose.prelive.yml is the production overlay; the name is historical").

**Proposed solution:** Rename the file to `docker-compose.prod.yml` and keep
`docker-compose.prelive.yml` as a symlink (or thin alias) for back-compat, so CI and existing
installs don't break during the migration.

**Why a symlink/back-compat step:** The filename is referenced in 8 places, incl. CI
(`.github/workflows/deploy-prelive.yml`) and the live hosts' `COMPOSE_FILE` env (LinPre1 prelive,
LinPre3 prod). A hard rename without an alias breaks the prelive auto-deploy and existing update
commands until every reference is updated.

**Impact / references to update** (grep `docker-compose.prelive.yml`):
- `.github/workflows/deploy-prelive.yml` (CI auto-deploy on LinPre1)
- `docker-compose.ghcr.yml` (usage comments)
- `tools/install/bootstrap-certs.sh`
- `docs/DEPLOYMENT.md`, `docs/DEPLOYMENT.de.md`, `README.md`, `TASKS.md`
- the overlay file itself

**Implementation slices:**
- [ ] `git mv docker-compose.prelive.yml docker-compose.prod.yml`; add a back-compat
      `docker-compose.prelive.yml` symlink → `docker-compose.prod.yml` (verify the Linux deploy
      hosts resolve the symlink; on Windows/git ensure it commits as a symlink, else keep a
      1-line duplicate during transition)
- [ ] Update CI `deploy-prelive.yml` to reference `docker-compose.prod.yml` (keep the prelive
      *environment*/workflow name — only the overlay file is renamed)
- [ ] Update all doc/script references (DEPLOYMENT.md/.de.md, README, `bootstrap-certs.sh`,
      `docker-compose.ghcr.yml` comments); switch the §6/§11 `COMPOSE_FILE` examples to
      `docker-compose.prod.yml` and drop the "historical name" clarifying comment
- [ ] Update existing hosts' `COMPOSE_FILE` (LinPre1, LinPre3) to the new name — or rely on the
      symlink during the transition
- [ ] After a transition period, remove the `prelive.yml` symlink/alias
- [ ] CHANGELOG entry

**Note:** The prelive *environment* and its deploy workflow stay named as-is — only the
**overlay file** is misnamed. The two concerns are independent.

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
