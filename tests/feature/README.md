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
| Entra group **revoke** (full chain) | `test_entra_group.py` | `DELETE /orders` → delete runbook → target_executor → graph_client → **mock Graph**; asserts a member-**remove** for the same group+user that was added |
| AD group **revoke** (real AD) | `test_ad_group.py` | provisioned order deleted (`deprovision_policy=access_only`) → **real DC** removes the member; change-log grant row flips `success → rolled_back`. Own isolated group so it can't disturb grant/drift |
| Capacity enforcement | `test_capacity.py` | `pool_capacity` full → 2nd order 409; `max_per_user` blocks the same user (409) but not a different one |
| ServiceNow webhook HMAC | `test_webhook.py` | `POST /webhook/servicenow` — valid `sha256=HMAC(body, WEBHOOK_SECRET_TOKEN)` → 201; bad sig → 401; no auth → 401; duplicate `servicenow_ref` → 409 |
| Expiry / reclaim Beat | `test_expiry.py` | busy pool asset past `expires_at` → `check_expiring_assets` (run in the worker) flips the order to `expired` and creates a `delete` reclaim order |
| Standalone runbook (ad-hoc) | `test_runbooks.py` | multi-step trigger → all steps `success` in order; self-contained pwsh modules, no external system |
| Standalone runbook (failure) | `test_runbooks.py` | critical step fails → run `failed`, later steps `skipped`, `always_run` finaliser still runs |
| Standalone runbook (cron) | `test_runbooks.py` | `check_cron_schedules` (run in the worker) dispatches a due `* * * * *` runbook → scheduled run → `success` |
| Composite order | `test_composite.py` | `POST /orders` → dynamic_runner composite → entra grant on **mock Graph** (GROUP_TARGETS) + asset-bound runbook step (RUNBOOK); both effects asserted |
| LDAP portal login (real AD) | `test_ldap.py` | `POST /portal/auth/ldap` → bogus creds 401; the configured bind account → 302 + session cookie (real NTLM bind vs winsrv1) |
| Teams delivery (real, no mock) | `test_teams.py` | `POST /admin/config/teams/test` → one real card to the live Teams webhook, asserts `ok:true` (2xx read-back); skips if Teams disabled |
| Attestation emission (worker) | `test_attestation_emit.py` | opted-in type: provision → worker emits **handover** (then acked via its own emitted token); delete → worker emits **revocation** cert |
| Access certification | `test_certification.py` | campaign scope → pending reviews; signed `/review/{token}` confirm keeps access; token revoke pulls the entra grant on **mock Graph** |
| HR leaver (bulk revoke) | `test_leaver.py` | `POST /hr/leaver` (scope `hr:leaver`) revokes all active orders at once — entra grant pulled on **mock Graph**, idempotent, 401/403 auth guards |
| Point-in-time access report | `test_access_report.py` | `GET /admin/access-report` replays `order_change_log` — live shows today's grant, `as_of` yesterday is empty, principal filter + CSV export |

**Revoke tests don't poll order status:** the cancel route flips the order to
`cancelled` (terminal) *before* the worker finishes revoking to `revoked`, so
the tests assert the real effect (mock member-remove / AD membership gone /
change-log `rolled_back`) instead of racing the status column.

**Default `max_per_user` is 1** — a webhook/capacity test that needs several
orders for one email must raise it on the asset type, or the per-user guard
masks the 409 you meant to assert (e.g. dup-ref idempotency).

Runbook/composite tests use **self-contained pwsh script modules** — each prints
exactly one JSON object and exits 0, so the runner decides success from the
`success` field with no external system in the loop. The purge covers
`script_modules` / `standalone_runbooks*` / `runbook_definitions+steps`
(deleted before `asset_types` — `runbook_definitions` FKs `asset_type_id`).

The **Teams** test is the only one that sends a real outward message — one test
card to the live channel via the admin test endpoint. It is deliberately never
pointed at the mock (unlike Slack/Graph), matching how Teams runs in this DEV
instance, and skips cleanly when Teams is disabled.
