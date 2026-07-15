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
- **Real AD** — AD-dependent tests (real `ad_group` grant, drift detection) run
  against the testlab DC (`winsrv1.xenpool.local`). The host isn't domain-joined,
  so AD ops execute inside the worker container (the `ad` fixture shells into
  `docker compose exec worker`); the whole file is skipped when AD is unreachable.
  Tests use an isolated auto-created zz-test group + existing test users
  (`john`, `jupp`) and delete the group on teardown. Drift runs in **detect_only**
  so the system-wide scan can never mutate a real monitored group.

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
| AD group grant (real AD) | `test_ad_group.py` | order → worker → target_executor → **real DC**; auto-creates group, asserts member present |
| Drift detection (real AD) | `test_ad_group.py` | out-of-band member injected → `reconcile_drift` (detect_only) records an out_of_band finding; managed member never flagged |

**Slice 4 (planned):** entra_group *revoke* through a delete order; AD group
revoke via a delete order + deprovision policy; LDAP portal login with a testlab
AD user (`auth.ldap_enabled=true`); Teams delivery assertion (real webhook,
read-back via 202) — kept separate so real Teams config is never redirected.
