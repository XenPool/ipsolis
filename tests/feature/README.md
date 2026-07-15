# Autonomous feature tests

Integration tests that drive the **running DEV compose stack** end-to-end over
HTTP + DB, and inspect the **testlab mock-receiver** for Slack / Teams / mock-Graph
delivery. Distinct from `api/tests` (pure unit tests, everything mocked, run in CI).

## What it needs running

```bash
# 1. the main app stack
docker compose up -d

# 2. the testlab mocks (Slack / mock-Graph / Teams / SIEM sink)
docker compose -f docker-compose.testlab.yml up -d mock-receiver
```

Credentials + DSN are read from the repo-root `.env` (`ADMIN_API_KEY`,
`API_SECRET_KEY`, `POSTGRES_*`), overridable via env vars
(`IPSOLIS_URL`, `IPSOLIS_MOCK_URL`, `IPSOLIS_DB_HOST/PORT`).

## Run

```bash
python -m venv .venv && . .venv/Scripts/activate     # or reuse any venv
pip install -r tests/feature/requirements.txt
cd tests/feature && python -m pytest -q
```

## Design

- **Namespaced + self-cleaning** — every row a test creates is prefixed `zz-test`
  (asset types, bundles, rules, contracts) or uses a `zz-test…@…` email. A
  session fixture purges the namespace before and after the run, so re-runs are
  idempotent and leave no residue.
- **Surgical config** — tests that need a mock (Slack, Graph) or a flag
  (`scim.joiner_enabled`) read the current value, set their own, and restore it.
  **Real SMTP + Teams config is never touched** — those are real integrations in
  this DEV instance.
- **Signed tokens** — the harness mints the same HMAC approval / attestation
  tokens the app does (from `API_SECRET_KEY`), so link-based flows complete
  without reading a mailbox.
- **`@requires_ad`** (planned, slice 2) — AD-dependent tests (drift, real
  ad_group grant/revoke) run against the testlab AD (`winsrv1.xenpool.local`) in
  DEV and are skipped where AD is unavailable.

## Coverage

| Area | File | Notes |
|---|---|---|
| SCIM filter grammar + /Groups shim | `test_scim.py` | eq/co/sw/and/pr, bad filter → 400 |
| SCIM joiner + leaver | `test_scim.py` | joiner orders a bundle (owner-approval type → parks, no dispatch); leaver revokes |
| Onboarding bundles + rules | `test_bundles.py` | CRUD, rule eval, idempotency skip |
| Software contracts (Model-A) | `test_contracts.py` | per-seat math (annual/quarterly/unlimited), validation |
| Approval via signed link | `test_approval.py` | `/approve/{token}` approve → order advances; decline → order rejected; bad token → 410 |
| Attestation handover ack | `test_approval.py` | `/attestation/{token}` GET + POST → status acknowledged |
| Entra group grant (full chain) | `test_entra_group.py` | order → worker → target_executor → graph_client → **mock Graph**; asserts member-add on the mock |
| Slack delivery | `test_notifications.py` | admin Slack test → Block Kit message reaches the **mock-receiver** |

**Slice 3 (planned):** entra_group *revoke* through a delete order; drift
reconcile against **real AD** (`@requires_ad`, testlab `winsrv1.xenpool.local`);
LDAP portal login with a testlab AD user; Teams delivery assertion (real webhook,
read-back via 202) — kept separate so real Teams config is never redirected.
