# ip·Solis — Disaster Recovery Runbook (Backup & Restore)

**Scope:** recovering a lost ip·Solis instance onto a **fresh host** from a database
backup. This is *disaster recovery*, not high availability — for hot failover see the
Postgres standby / multi-instance sections of [DEPLOYMENT.md](DEPLOYMENT.md).

For an **in-place** restore between backups on a still-running instance (e.g. undoing a
bad change), use **Admin → Maintenance → Backups → Restore** instead — it takes a
pre-restore safety backup automatically. This runbook is for the case where the box
itself is gone.

> **Status:** exercised by hand once (doc-only task). There is deliberately no automated
> restore test — see [AUDIT-FINDINGS.md](../AUDIT-FINDINGS.md) A4.

---

## 1. What a backup does and does not contain

ip·Solis backups are `pg_dump` (plain SQL, `--no-owner --no-privileges`) piped through
gzip, written to `./backups/` on the host as `xp_backup_<timestamp>.sql.gz`
(worker: [`maintenance.py`](../worker/tasks/modules/maintenance.py) `_run_backup_sync`).

**In the dump (restored automatically):**
- All application data: `orders`, `asset_pool`, `asset_types`, `audit_log`, runbooks, …
- **`app_config` — including external-system credentials in cleartext.** Passwords for
  AD, SMTP, vSphere/XenServer, SCCM etc. are stored as plaintext in `app_config.value`
  ([`config.py`](../api/app/models/config.py) — `is_secret` only masks them in the UI, it
  does **not** encrypt). So after a restore, **email and AD access work immediately** —
  you do not re-enter those credentials.
- `admin_users` (hashed passwords) and `api_tokens` (hashed) — admins can log in again.

**NOT in the dump (must be carried over / re-created separately):**
- **`API_SECRET_KEY`** — lives in `.env`, not the database. It signs approval &
  certification token URLs. If the new box uses a *different* key, already-sent signed
  links (approval/certification emails and Teams cards) become invalid; newly issued ones
  are fine. **Carry the same `API_SECRET_KEY` over** to keep in-flight links working.
- **Externalized secrets** — if the tenant stores credentials in an external vault, the
  dump contains only the *reference* (`vault://`, `ccp://`, `azurekv://`, `awssm://`,
  `conjur://` — [`secrets.py`](../api/app/utils/secrets.py)), not the value. See §5.
- **`.env`** infra config generally (DB user/password, ports, broker URLs).
- **TLS certificates** (`certs/`) and the **commercial license** (`licenses/*.lic`).
- The **backup files themselves** (`backups/`) — keep an off-box copy of the latest dump.

---

## 2. Prerequisites on the recovery side

Have these available *before* you start (ideally stored off-box / in your secret manager):

- [ ] The latest backup file, e.g. `xp_backup_20260714_020000.sql.gz`.
- [ ] The old `.env` — or at least the same values for `API_SECRET_KEY`,
      `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`, `ADMIN_API_KEY`,
      `WEBHOOK_SECRET_TOKEN`, and the `CELERY_*` URLs.
- [ ] The deploy directory / compose files (`docker-compose.yml` + `docker-compose.prod.yml`).
- [ ] TLS certs (`certs/`), or accept a fresh self-signed cert (`tools/install/bootstrap-certs.sh`).
- [ ] The `.lic` license file (`licenses/`), for Pro deployments.
- [ ] If you use an external secret store: network reachability from the new box + the
      `secret.*` backend config values (§5).

---

## 3. Restore procedure (fresh host → stack → DB restore)

Run from the deploy directory on the new host. Commands assume the default
`POSTGRES_USER=xpuser` / `POSTGRES_DB=ipsolis`; adjust if your `.env` differs.

### 3.1 Prepare the host

```bash
# Docker + Docker Compose installed, deploy dir in place
cd /opt/ipsolis
cp /secure/location/.env .env          # carry over the OLD .env (same API_SECRET_KEY!)
cp /secure/location/xp_backup_*.sql.gz backups/
# certs: restore certs/ OR generate a fresh self-signed cert
bash tools/install/bootstrap-certs.sh  # no-op if certs/ already present
```

### 3.2 Bring up the database only, then load the dump

Load into a clean database **before** the app or migrations touch it.

```bash
# 1. Start only Postgres
docker compose up -d postgres

# 2. Wait until it is ready
until docker compose exec -T postgres pg_isready -U xpuser -d ipsolis; do sleep 2; done

# 3. Recreate an empty target DB (clean slate, mirrors the in-app restore)
docker compose exec -T postgres psql -U xpuser -d postgres \
  -c 'DROP DATABASE IF EXISTS ipsolis;' \
  -c 'CREATE DATABASE ipsolis OWNER xpuser;'

# 4. Load the gzipped SQL dump
gunzip -c backups/xp_backup_20260714_020000.sql.gz | \
  docker compose exec -T postgres psql -U xpuser -d ipsolis --set ON_ERROR_STOP=1
```

### 3.3 Start the rest of the stack and apply migrations

```bash
# 5. Start api, worker, beat, nginx, redis, …
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 6. Apply migrations — the dump may be from an OLDER schema than the current image.
#    Alembic skips already-applied migrations, so this is always safe to run.
docker compose exec -T api alembic upgrade head

# 7. (optional) restart api + worker so they pick up the restored data cleanly
docker compose restart api worker
```

> **Why this order:** on a fresh box the app must not create its own empty tables before
> the dump is loaded. Load the dump into a clean DB first, *then* run
> `alembic upgrade head` to close any schema gap between the dump and the running image.

---

## 4. Post-restore steps

- [ ] **API tokens** — the `api_tokens` table is restored with the DB. Review under
      **Admin → API Tokens**; revoke old/unused tokens and issue fresh ones for active
      integrations only (see [DEPLOYMENT.md](DEPLOYMENT.md) §7).
- [ ] **`API_SECRET_KEY`** — confirm the new `.env` carries the **same** key as the old
      box. If it changed, tell approvers that older approval/certification email links no
      longer open; re-trigger those notifications so fresh signed links are issued.
- [ ] **TLS certs** — real CA certs restored into `certs/`, or the self-signed fallback
      accepted. `docker compose restart nginx` after replacing certs.
- [ ] **License** — `.lic` file present in `licenses/` (Pro deployments).
- [ ] **Externalized secrets** — only if you use a vault: verify reachability (§5).

---

## 5. Externalized-secret case (Vault / CyberArk / Azure KV / AWS SM / Conjur)

Only relevant if credentials were stored as references (`vault://…`, `ccp://…`, etc.)
rather than plaintext. For the default (plaintext-in-`app_config`) deployment, **skip
this section** — the credentials came back with the dump.

If you do use an external store, after the restore:

- [ ] The new host has **network access** to the secret backend.
- [ ] The `secret.*` backend configuration resolved correctly. It lives in `app_config`
      and was restored — but a Vault **token** stored there may have expired, or the new
      box may have a different identity (AppRole / Kubernetes JWT / AWS role).
- [ ] Verify from the UI: **Admin → Settings → Compliance → Secret backend → Test**
      (backend reachability), then a per-integration test (AD / SMTP) to confirm a real
      secret resolves. All test endpoints are listed in [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 6. Verification checklist (tick off after restore)

- [ ] **Health**: `curl -fsk https://YOUR_HOST/health` returns `{"status": "ok"}`.
- [ ] **Admin login**: an existing admin (from the restored DB) can sign in at `/ui/`.
- [ ] **Data spot-check**: order count and asset pool on the dashboard match expectations.
- [ ] **AD lookup**: on the order form, user validation (deputy / RDP / admin fields)
      resolves names — proves the restored AD credentials work.
- [ ] **Email**: submit a test order and confirm the notification email arrives — proves
      the restored SMTP credentials work.
- [ ] **Approval link**: open the signed review link from an approval email / Teams card —
      proves `API_SECRET_KEY` was carried over correctly (a changed key makes old links
      fail; newly issued ones still work).
- [ ] **Portal login**: an OIDC provider **Test** passes and a real login completes.

---

## 7. Rollback

- **In-app restore** (Admin → Maintenance) always takes a pre-restore safety backup
  (`xp_backup_pre_restore_<timestamp>.sql.gz`) before overwriting — restore *that* to undo.
- **This CLI DR path**: keep the previous dump. To roll back, repeat §3.2 with the earlier
  file.

---

## 8. Related

- [DEPLOYMENT.md](DEPLOYMENT.md) — full production deployment, backup scheduling, secret
  backends, per-integration test endpoints.
- [onboarding/INSTALL.md](onboarding/INSTALL.md) — quick backup/restore pointers.
- German version: [DR-RUNBOOK.de.md](DR-RUNBOOK.de.md).
