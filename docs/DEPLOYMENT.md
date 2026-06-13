# ip·Solis -- Production Deployment Guide

This guide walks you through setting up the ip·Solis platform on a fresh on-premises server. No prior knowledge of the codebase is required.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Get the Software](#2-get-the-software)
3. [Configure Environment Variables](#3-configure-environment-variables)
4. [SSL / TLS Certificate Setup](#4-ssl--tls-certificate-setup)
5. [Create the Production Compose Overlay](#5-create-the-production-compose-overlay)
6. [Start the Stack](#6-start-the-stack)
7. [Initial Admin Setup](#7-initial-admin-setup)
   - [Install Your License (Pro)](#install-your-license-pro)
8. [Entra ID SSO (Portal Authentication)](#8-entra-id-sso-portal-authentication)
9. [Verify the Deployment](#9-verify-the-deployment)
10. [Backup & Maintenance](#10-backup--maintenance)
11. [Updating to a New Version](#11-updating-to-a-new-version)
12. [High-Availability Deployments](#12-high-availability-deployments)
13. [Troubleshooting](#13-troubleshooting)
14. [Clean Reset (Test Environments)](#14-clean-reset-test-environments)

---

---

## 1. Prerequisites

### Server Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Linux (Debian/Ubuntu recommended) | Ubuntu 22.04 LTS or newer |
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB | 50 GB (depends on number of managed assets) |

### Software

Install the following before proceeding:

- **Docker Engine** >= 24.0 -- [Install Docker](https://docs.docker.com/engine/install/)
- **Docker Compose** >= 2.20 (included with Docker Engine)
- **Git** -- to clone the repository

After installing Docker, add the deployment user to the `docker` group so
`docker compose` commands work without `sudo`:

```bash
sudo usermod -aG docker $USER
# Then log out and back in (or: newgrp docker)
```

Verify your installation:

```bash
docker --version        # Docker version 24.x or higher
docker compose version  # Docker Compose version v2.20 or higher
git --version
```

### Network Requirements

The server needs outbound access to:

| Destination | Purpose |
|-------------|---------|
| Your Active Directory / LDAP server (port 389 or 636) | User validation, manager lookup, group membership |
| Your SMTP relay | Email notifications |
| vSphere / XenServer (if applicable) | VM lifecycle automation |
| SCCM server (if applicable) | Task sequence triggers |

Inbound: ports **80** and **443** must be reachable from your users' browsers.

---

## 2. Get the Software

> **Fresh environment recommended:** Docker volumes (database data) survive
> `rm -rf /opt/ipsolis` — they live under `/var/lib/docker/volumes/` and persist
> until explicitly removed. For a clean first install, ensure no old volumes exist.
> See [Clean Reset (Test Environments)](#14-clean-reset-test-environments).

Clone the repository and pull the images — no authentication required:

```bash
cd /opt
sudo git clone https://github.com/XenPool/ipsolis.git ipsolis
cd ipsolis
```

The Docker images (`ghcr.io/xenpool/ipsolis-api` and
`ghcr.io/xenpool/ipsolis-worker`) are public and pulled automatically when
you start the stack.

> **Licensing:** ip·Solis is free for non-commercial and evaluation use.
> Commercial use requires a license — see [LICENSE](../LICENSE) and
> contact **sales@xenpool.de** to purchase.

---

## 3. Configure Environment Variables

Copy the example file and edit it:

```bash
sudo cp .env.example .env
sudo nano .env
```

### Required settings to change

```ini
# Secure database credentials
POSTGRES_PASSWORD=<generate-a-strong-password>

# Secure API secrets -- use random strings of 32+ characters
API_SECRET_KEY=<random-string-min-32-chars>
WEBHOOK_SECRET_TOKEN=<random-string>
ADMIN_API_KEY=<random-string-min-32-chars>

# CORS -- set to your production domain  ← replace YOUR_HOSTNAME.YOUR_COMPANY.COM
CORS_ORIGINS=https://YOUR_HOSTNAME.YOUR_COMPANY.COM
FLOWER_PASSWORD=<strong-password>
```

> **Tip**: Generate secure passwords with:
> ```bash
> openssl rand -base64 32
> ```

## 4. SSL / TLS Certificate Setup

The platform runs behind an nginx reverse proxy that terminates SSL. You need a TLS certificate and private key.

### Option A: Internal / Self-Signed Certificate (Intranet)

If your server is only accessible within your corporate network, use [mkcert](https://github.com/FiloSottile/mkcert) to generate a trusted certificate:

```bash
# Install mkcert (one-time)
# Ubuntu/Debian:
sudo apt install -y libnss3-tools
sudo curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
chmod +x mkcert-v*-linux-amd64
sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert

# Install the local CA into your system trust store
sudo mkcert -install

# Generate the certificate for your hostname  ← replace YOUR_HOSTNAME.YOUR_COMPANY.COM
sudo mkdir -p certs
sudo mkcert -cert-file certs/cert.pem -key-file certs/key.pem YOUR_HOSTNAME.YOUR_COMPANY.COM
```

> **Important**: For browsers on other machines to trust this certificate, you must
> distribute the root CA (`mkcert -CAROOT` shows the path) to client machines via
> Group Policy or your enterprise CA trust store.

**Installing the root CA on a Windows client:**

```bash
# On the server — make the root CA available for download
sudo cp $(sudo mkcert -CAROOT)/rootCA.pem /tmp/ipsolis-rootCA.pem
sudo chmod 644 /tmp/ipsolis-rootCA.pem
```

Copy the file to your Windows laptop (SCP, USB, etc.), then:

**Option 1 — via double-click:**
1. Rename the file to `ipsolis-rootCA.crt`
2. Double-click → **Install Certificate**
3. **Local Machine** → **Trusted Root Certification Authorities**
4. Restart your browser

**Option 2 — via PowerShell (as Administrator):**
```powershell
certutil -addstore -f "ROOT" ipsolis-rootCA.crt
```

After installation Chrome, Edge and Firefox (using the Windows trust store) will trust the certificate without warnings.

### Option B: Certificate from your Enterprise CA (Recommended for production)

If your organization runs an internal Certificate Authority (e.g., Active Directory Certificate Services):

1. Generate a CSR on the server: *(replace YOUR_HOSTNAME.YOUR_COMPANY.COM)*
   ```bash
   sudo mkdir -p certs
   sudo openssl req -new -newkey rsa:2048 -nodes \
     -keyout certs/key.pem \
     -out certs/server.csr \
     -subj "/CN=YOUR_HOSTNAME.YOUR_COMPANY.COM"
   ```
2. Submit `certs/server.csr` to your CA and obtain the signed certificate.
3. Save the signed certificate as `certs/cert.pem`.
4. If your CA provides an intermediate/chain certificate, append it to `cert.pem`:
   ```bash
   cat signed-cert.pem intermediate-ca.pem | sudo tee certs/cert.pem > /dev/null
   ```

### Option C: Let's Encrypt (Public-facing servers)

If your server is publicly accessible, you can use free certificates from Let's Encrypt:

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d YOUR_HOSTNAME.YOUR_COMPANY.COM  # ← replace

# Symlink into the certs directory
sudo mkdir -p certs
sudo ln -sf /etc/letsencrypt/live/YOUR_HOSTNAME.YOUR_COMPANY.COM/fullchain.pem certs/cert.pem
sudo ln -sf /etc/letsencrypt/live/YOUR_HOSTNAME.YOUR_COMPANY.COM/privkey.pem certs/key.pem
```

#### Set up auto-renewal (Option C only)

```bash
# Test renewal
sudo certbot renew --dry-run

# Add a cron job to reload nginx after renewal
echo "0 3 * * * certbot renew --quiet --post-hook 'docker exec ipsolis-nginx nginx -s reload'" | sudo crontab -
```

### Configure nginx

The repository already ships a ready-to-use `nginx/nginx.conf` with the placeholder `YOUR_HOSTNAME.YOUR_COMPANY.COM`. Replace it with your actual FQDN (`sed` handles both occurrences in one pass):

```bash
sudo sed -i 's/YOUR_HOSTNAME.YOUR_COMPANY.COM/ipsolis.acme.com/g' nginx/nginx.conf
```

The file will look like this afterwards (for reference):

```nginx
server {
    listen 80;
    server_name YOUR_HOSTNAME.YOUR_COMPANY.COM;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name YOUR_HOSTNAME.YOUR_COMPANY.COM;

    ssl_certificate     /etc/nginx/certs/cert.pem;
    ssl_certificate_key /etc/nginx/certs/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 2g;

    # WebSocket / HTMX support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    location / {
        proxy_pass         http://api:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

> Use the same hostname in the certificate generation step (Option A/B/C above).

---

## 5. Production Compose Overlay

`docker-compose.prod.yml` is already included in the repository — no action needed.
The overlay adds nginx for SSL termination and removes the dev bind-mounts from
`api` and `worker`.

---

## 6. Start the Stack

```bash
cd /opt/ipsolis

# Build and start all services
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up --build -d

# Run database migrations
docker compose exec -T api alembic upgrade head

# Verify all containers are running
docker compose ps
```

Expected output -- all services should show `Up (healthy)`:

```
NAME             STATUS
ipsolis-postgres      Up (healthy)
ipsolis-redis         Up (healthy)
ipsolis-api           Up (healthy)
ipsolis-worker        Up (healthy)
ipsolis-beat-1   Up
ipsolis-nginx         Up
```

Verify the application:

```bash
# Direct API health check
curl -f http://localhost:8000/health | python3 -m json.tool

# Through nginx (HTTPS)
curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health | python3 -m json.tool
```

---

## 7. Initial Admin Setup

### First-run admin account (RBAC)

Open **https://YOUR_HOSTNAME.YOUR_COMPANY.COM/ui/** in your browser. On
the very first visit (when `admin_users` is empty), the login page
renders a **"Create first administrator"** form instead of the
normal sign-in form. Fill in:

| Field | Notes |
|---|---|
| Username | 3–128 chars, allowed: `[a-zA-Z0-9._@-]+`. Lower-cased at write time. |
| Password | ≥ 12 chars. PBKDF2-SHA256 / 600k iterations (OWASP-2023). |
| Confirm password | Must match. |

Submitting creates the first **superadmin** and auto-logs you in.
This is idempotent against races — if two operators hit the form at
the same time, only one wins; the other gets a "use the sign-in
form" message.

After the first superadmin exists, the form switches to the regular
username + password sign-in.

### Add additional admin users

Once signed in, navigate to **Admin Users** in the left nav
(superadmin-only). Create per-user accounts in the role appropriate
to each operator:

```
superadmin > admin > approver > auditor > helpdesk
```

The full role ladder, per-asset-type ACL grants, separation-of-duties
enforcement, and password-policy options are configurable in the Admin UI
under Settings → Access Control.

### Legacy `ADMIN_API_KEY` fallback

The `ADMIN_API_KEY` from `.env` continues to authenticate as a
**virtual superadmin** even after first-run setup, so existing
scripts / `X-Admin-Key` headers don't break on upgrade. To use it
on the login page: leave **Username** blank, paste the key into
**Password**. Audit attribution shows up as `admin:legacy_key` so
auditors can tell when the fallback path was used.

For new integrations prefer **Per-integration API tokens** (Admin UI
→ *API Tokens*) — named, expiring, revocable bearer tokens with
optional role binding and scoped permissions. The legacy single
shared key is kept for back-compat only.

### Install Your License

Evaluation and non-commercial use require no license file. For commercial
deployments, XenPool delivers a signed `.lic` file after purchase.

Install it through the Admin UI:

1. Navigate to **Admin → License** (or open
   `https://YOUR_HOSTNAME.YOUR_COMPANY.COM/ui/license`).
2. Click **Upload license** and select your `ipsolis.lic` file.
3. The page reloads showing licensee name and expiry — no restart required.

**Grace period**: when a license expires, a 30-day grace period applies
before the license status reverts to unlicensed. The Admin UI shows an
amber warning banner and the daily health alert email fires each day
throughout the window.

**Overwriting**: upload a new `.lic` at any time to renew. The old file
is replaced in-place; the license cache refreshes on the next request
(mtime-keyed, zero downtime).

**Env-var override** (air-gapped / automated deployments): mount the
`.lic` file into the container at an alternate path and set:

```bash
IPSOLIS_LICENSE_PATH=/run/secrets/ipsolis.lic
```

The default path is `/app/license/ipsolis.lic` (inside the `ipsolis-api`
container). Docker secrets or a bind-mount both work.

### Configuration Checklist

The in-app **Setup checklist** on the dashboard guides you through all required steps.
The order below matches the checklist:

#### 1. Set application title and logo *(Essential)*

Navigate to **Admin > Settings → General**:

| Setting | Description |
|---------|-------------|
| `app.title` | Application name shown in the portal and emails (default: `ip·Solis`) |
| `app.logo` | Logo upload (PNG/SVG recommended) |

#### 2. Configure SMTP *(Essential)*

Navigate to **Admin > Settings → Email**:

| Setting | Description | Example |
|---------|-------------|---------|
| `smtp.host` | SMTP relay hostname | `smtp.yourcompany.com` |
| `smtp.port` | SMTP port | `587` |
| `smtp.user` | SMTP username (if auth required) | `selfservice@yourcompany.com` |
| `smtp.password` | SMTP password | *(marked as secret)* |
| `smtp.tls` | Use STARTTLS | `true` |
| `smtp.from` | Sender email address | `noreply@yourcompany.com` |
| `smtp.from_name` | Sender display name | `ip·Solis` |

Navigate to **Admin > Email Templates** to customize notification email text.

#### 3. Connect to Active Directory *(Essential)*

Navigate to **Admin > Settings → Active Directory**:

| Setting | Description | Example |
|---------|-------------|---------|
| `ad.server` | AD domain controller hostname or IP | `dc01.yourcompany.com` |
| `ad.port` | LDAP port | `389` (or `636` for LDAPS) |
| `ad.base_dn` | Search base DN | `DC=yourcompany,DC=com` |
| `ad.domain` | NetBIOS domain name | `YOURCOMPANY` |
| `ad.username` | Service account (sAMAccountName) | `svc-selfservice` |
| `ad.password` | Service account password | *(marked as secret)* |
| `ad.use_ssl` | Use LDAPS | `true` or `false` |

> Required AD permissions depend on the modules and runbook steps in use.
> As a baseline:
> - **Read** on user objects (attributes: `mail`, `displayName`, `sAMAccountName`,
>   `userPrincipalName`, `manager`, `memberOf`, `distinguishedName`)
> - **Write `member`** on group objects — required for AD group-based access assignment
>
> Additional permissions (e.g. on computer objects, OUs, or other attributes) may be
> needed depending on the runbooks and modules deployed.

#### 4. Enable portal SSO via Entra ID *(Essential)*

See [Section 8](#8-entra-id-sso-portal-authentication) for the full Entra ID setup.

#### 5. Create your first asset type *(Essential)*

1. Go to **Admin > Asset Types > New**
2. Fill in the name, description, and category
3. Configure the automation strategy (Group Access, Runbook, or Composite)
4. Set approval requirements if needed
5. Optionally restrict access with an Eligible Requestors group DN
6. Save

#### 6. Add at least one asset to the pool *(Essential)*

Go to **Admin > Asset Pool > New** and add at least one asset.

> For pure `capacity_pooled` asset types (quota without dedicated instances) this
> step can be skipped.

#### Set up Runbooks *(if applicable)*

ip·Solis ships with a fully configured example runbook:
**"Virtual Machine Recycler"** — a standalone runbook that includes all required
script modules (XenServer/XCP-ng, SCCM, Active Directory) and can serve as a
template for your own automation.

Find it under **Admin > Runbooks** to inspect, copy, or adapt it.

To create asset-type runbooks:

1. Go to **Admin > Runbooks > New**
2. Define the steps (PowerShell modules or built-in modules)
3. Link the runbook to an asset type

Any number of custom runbooks with any combination of steps can be created.

#### Recommended next steps

- **Microsoft Teams approval cards**: Go to **Admin > Settings → Email** and add a
  Teams webhook URL — approvers receive an Adaptive Card with a one-click review
  link in addition to email.
- **Stream audit log to SIEM**: Configure a Splunk HEC or webhook endpoint under
  **Admin > Settings → Compliance**.
- **Issue per-integration API tokens**: Go to **Admin > API Tokens** to create named,
  revocable bearer tokens for ServiceNow, scripts, or Prometheus — replaces the
  shared `X-Admin-Key`.

> **After a DB restore:** The `api_tokens` table is restored along with the database.
> Review all tokens under **Admin > API Tokens** — revoke any old or unused tokens
> and issue new, dedicated tokens for active integrations only.

---

## 8. Entra ID SSO (Portal Authentication)

The self-service portal supports Microsoft Entra ID (Azure AD) for single sign-on.

### Register an App in Entra ID

1. Go to the [Azure Portal](https://portal.azure.com) > **App registrations** > **New registration**
2. Name: `ip·Solis`
3. Redirect URI: `https://YOUR_HOSTNAME.YOUR_COMPANY.COM/portal/auth/callback` (Web)
4. Note down the **Application (client) ID** and **Directory (tenant) ID**
5. Under **Certificates & secrets**, create a new client secret

### Configure in Admin UI

Navigate to **Admin > Settings** and set:

| Setting | Description |
|---------|-------------|
| `entra.mode` | `entra_only` (Entra ID login required) or `entra_with_onprem` (Entra ID + on-prem LDAP check) |
| `entra.client_id` | Application (client) ID |
| `entra.client_secret` | Client secret value *(marked as secret)* |
| `entra.tenant_id` | Directory (tenant) ID |
| `entra.redirect_uri` | `https://YOUR_HOSTNAME.YOUR_COMPANY.COM/portal/auth/callback` *(replace)* |
| `entra.allowed_domains` | Comma-separated list of allowed email domains, e.g. `yourcompany.com` |

Use the **Test Entra Connection** button to verify the configuration.

> When `entra.mode` is set to `disabled`, the portal is open to anyone
> on the network with a shared anonymous identity — every visitor sees
> and can act on the same set of orders. Only use this for demo /
> air-gapped lab deployments. For multi-user production, set
> `entra.mode = entra_only`.

---

## 9. Verify the Deployment

Run through this checklist to confirm everything works:

- [ ] **HTTPS**: `https://YOUR_HOSTNAME.YOUR_COMPANY.COM` loads with a valid certificate
- [ ] **Admin UI**: `https://YOUR_HOSTNAME.YOUR_COMPANY.COM/ui/` is accessible
- [ ] **First-run setup**: visiting the admin login renders the "Create first administrator" form (or, if already done, the regular sign-in form with no error)
- [ ] **Setup checklist**: the dashboard shows the in-app setup checklist; tick off Essential items as you configure them
- [ ] **Portal login**: Users can sign in via Entra ID SSO
- [ ] **AD lookup**: On the order form, user validation (deputy, RDP, admin fields) resolves names
- [ ] **Email**: Submit a test order and confirm notification email arrives
- [ ] **Health check**: `curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health` returns `{"status": "ok"}`
- [ ] *(optional)* **API tokens**: issue a per-integration token for any automation that previously used `X-Admin-Key`
- [ ] *(optional)* **SIEM streaming**: configure under *Settings → Compliance* if you have Splunk / Sentinel / a generic webhook receiver
- [ ] *(optional)* **Prometheus**: scrape `/metrics` from your monitoring; the dashboard ships in [docs/grafana/](grafana/)

---

## 10. Backup & Maintenance

### Database Backup

The PostgreSQL data is stored in a Docker volume (`postgres_data`). Back it up regularly:

```bash
# Dump the database
docker compose exec -T postgres pg_dump -U xpuser ipsolis > backup_$(date +%Y%m%d).sql

# Restore from backup
cat backup_20260414.sql | docker compose exec -T postgres psql -U xpuser ipsolis
```

### Logs

View container logs:

```bash
# All services
docker compose logs --tail=50

# Specific service
docker compose logs api --tail=100 -f    # follow mode
docker compose logs worker --tail=100
```

### Disk Cleanup

Periodically remove old Docker images:

```bash
docker image prune -f
```

---

## 11. Updating to a New Version

```bash
cd /opt/ipsolis

# Pull the latest code
git pull origin main

# Rebuild and restart
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up --build -d

# Run any new database migrations
docker compose exec -T api alembic upgrade head

# Restart nginx to pick up new container IPs and any config changes
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  restart nginx

# Verify health
curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health | python3 -m json.tool
```

> Migrations are safe to run multiple times -- Alembic tracks which have
> already been applied and skips them. Each feature slice typically
> ships its own migration; review `api/alembic/versions/` between
> upgrades for the changeset, and `docker compose exec api alembic
> history` to see the chain.

### Backing up before upgrade

Always snapshot the database first — `pg_dump` from the Postgres
container, or use the in-app **Maintenance → Backups** page (Admin UI)
which writes a timestamped SQL dump to the bind-mounted `./backups/`
directory. Configure a daily backup schedule in the same UI so the
snapshot is fresh when an unexpected regression appears.

### Beat HA failover during the restart

If you run multiple Beat replicas (`--scale beat=N`), `docker compose
up --build -d` rolls the containers one at a time and the leader lock
hands over to the surviving replica within ~13 s.
For single-Beat installs there's a brief gap during the restart
where periodic tasks aren't running — usually invisible since cadences
are minutes / hours.

---

## 12. High-Availability Deployments

ip·Solis scales horizontally at the API and worker layers. The Beat scheduler
supports multi-replica HA via celery-redbeat. This section covers the two
tested scaling scenarios: API replicas and worker replicas.

### 12.1 Multi-replica API

The API is **stateless** by design — every replica handles every
request equally and there's no need for sticky-session affinity at
the load balancer.

**What makes it stateless**:

* Sessions use Starlette's
  [`SessionMiddleware`](https://www.starlette.io/middleware/#sessionmiddleware)
  in cookie-signed mode (`api/app/main.py`): the entire session
  payload (admin user id, role, csrf token) lives in the
  `xp_session` cookie itself, signed with `API_SECRET_KEY`. No
  server-side session table.
* Tokenized URLs (`/approve/<token>`,
  `/portal/certifications/review/<token>`, etc.) are HMAC-signed
  with the same `API_SECRET_KEY` and verify-only. No replay table.
* All request state lives in Postgres or Redis — both shared
  across replicas.

**What every replica MUST share**:

| What | Why | How |
|---|---|---|
| `API_SECRET_KEY` | Signs session cookies + approval tokens. Different keys per replica = clients see "session invalid" / "approval link expired" half the time. | Pin in `.env`; load via `env_file:` in compose so every replica reads the same file. |
| `DATABASE_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Shared Postgres + Redis backplane. | Same as above. |
| Shared filesystem mounts | `licenses/`, `scripts/`, `backups/` are bind-mounted; replicas reading the same paths must see the same content. On a single host, that's automatic. On multiple hosts, use NFS / GlusterFS / a shared volume driver — or migrate the relevant content to S3-compatible object storage (a deferred slice). | Single-host deployments don't need any extra plumbing. |

**Scaling commands**:

```bash
# Single-host: bump the api replica count via compose
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --scale api=3

# Verify each replica is reachable through the load balancer
for i in 1 2 3; do
  curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health \
    -H 'X-Replica-Probe: '$i
done
```

**Load-balancer config notes**:

* **No sticky sessions required**. Round-robin or least-connections
  is fine.
* **Health check**: `GET /health` (unauthenticated). Returns
  `{status: ok | degraded}` aggregating database, redis, and beat
  liveness. The endpoint is fast (one Redis ping + one
  DB SELECT 1) so a 5–10s LB check interval is safe.
* **TLS termination**: keep on the load balancer (or the existing
  nginx sidecar from section 5). Replicas serve plain HTTP
  internally; the
  [`https_only=True`](https://www.starlette.io/middleware/#sessionmiddleware)
  flag on `SessionMiddleware` guards the cookie's `Secure` bit
  irrespective of where TLS terminates.

**Rolling restart during upgrades**: the upgrade flow in section 11
stops and restarts every replica together, which is fine for small
fleets where ~30s of API downtime is acceptable. For zero-downtime
rolls, fold the `up --build -d` step into a per-replica loop:

```bash
for i in 1 2 3; do
  docker compose stop api-$i
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    up --build -d --no-deps api-$i
  # Wait for the new container to pass health
  until curl -fsk http://localhost/health > /dev/null 2>&1; do
    sleep 2
  done
done
```

This requires an LB that can drain one backend at a time; with the
default round-robin nginx upstream, in-flight requests on the
restarting replica drop. The drain logic is your LB's responsibility.

### 12.2 Multi-replica worker

Celery workers are stateless consumers — they pull from the named
Redis queues and process tasks. Adding more workers is a one-line
scale-up; the worker code itself doesn't change.

**Queue topology** (defined in `worker/tasks/__init__.py`):

| Queue | Tasks | Why a separate queue |
|---|---|---|
| `provision` | Order workflows (`dynamic_runner`, `standalone_runner`, `ps_module_installer`, `sccm_probe`) — anything that touches AD / SCCM / vSphere / XenServer. | Provisioning steps shell out to PowerShell (~5–60s/step) and hold connections to external systems. Isolating them keeps a slow vSphere call from blocking quick housekeeping tasks. |
| `notifications` | Email senders, Teams card delivery, approval reminders, certification reminders, cost alerts. | I/O-bound, latency-sensitive (a stuck SMTP server shouldn't queue up behind a 30s SCCM probe). |
| `default` | Audit retention prune, SIEM streaming, license check, update checker, cost-report snapshot, DB backup, **api token purge**. | Background housekeeping. Mostly cron-driven, low-frequency. |
| `reclaim` | Asset-expiry checks (`check_expiring_assets`). | Hourly Beat task; small but isolated so the hourly tick doesn't compete with order workflows for a worker slot. |

**Sizing recommendations** (per-queue concurrency × replica count):

| Pool size | Recommended config | Reasoning |
|---|---|---|
| Lab / single-team (≤50 users) | 1 worker replica, `--concurrency=4 -Q provision,notifications,default,reclaim` | All queues on one process; concurrency 4 is plenty for the typical 1–2 orders/hour. |
| Mid (≤500 users, ≤20 orders/hour) | 2 worker replicas split by queue: replica A `-Q provision --concurrency=4`, replica B `-Q notifications,default,reclaim --concurrency=2` | Provisioning latency stays bounded by replica A; replica B handles housekeeping + reminders without queue-head-of-line blocking. |
| Large (≥500 users, ≥50 orders/hour, regulated SLAs) | 3+ worker replicas: dedicated `provision` workers (`--concurrency=8` × 2 replicas), one `notifications` replica (`--concurrency=4`), one `default,reclaim` replica (`--concurrency=2`) | Per-queue scaling matches actual load shape. |

**Scaling command** (single-host, all queues on each replica):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --scale worker=3
```

**Per-queue dedicated replicas** require either separate compose
service definitions (e.g. `worker-provision`, `worker-notifications`)
each with its own `command:` overriding the default queue list, or
a runtime `command:` override:

```yaml
# docker-compose.prod.yml — per-queue split
services:
  worker-provision:
    image: ipsolis-worker
    command: celery -A tasks worker -Q provision --concurrency=8 -l info
    deploy: { replicas: 2 }
    env_file: .env

  worker-notifications:
    image: ipsolis-worker
    command: celery -A tasks worker -Q notifications --concurrency=4 -l info
    deploy: { replicas: 1 }
    env_file: .env

  worker-housekeeping:
    image: ipsolis-worker
    command: celery -A tasks worker -Q default,reclaim --concurrency=2 -l info
    deploy: { replicas: 1 }
    env_file: .env
```

**Beat scaling**: the beat container has no fixed `container_name` so it can be
replicated for HA:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d --scale beat=2
```

> **Note**: Celery Beat is a singleton scheduler. Multiple beat replicas only make
> sense with a distributed lock backend — `celery-redbeat` (already configured) uses
> Redis locks to prevent duplicate task firing.

**Liveness**: each worker registers with Celery's mingle-on-startup,
which means a fresh worker is visible to Beat / other workers within
a couple of seconds. There's no separate health check to wire — if
the worker container is `Up`, it's consuming.

**Visibility**: Flower (the existing `flower` service in the dev
compose; see `docker-compose.yml`) shows live worker registration,
queue depth, and task-by-task duration breakdown. For production,
front it with the same nginx auth as the admin UI; Flower has no
built-in authn beyond HTTP basic.

### 12.3 Postgres high-availability

Postgres HA (streaming replication, pgBackRest, Patroni) is architecturally
possible — ip·Solis is single-primary and any connection-string switch requires
only a `.env` change and restart. A validated step-by-step guide is not included
in this version.

---

## 13. Troubleshooting

### Container won't start

```bash
# Check container status and exit codes
docker compose ps -a

# Check logs for the failing service
docker compose logs <service-name> --tail=50
```

### Health check fails through nginx but API is healthy

Nginx may have cached the old container IP. Restart the container
(not just `nginx -s reload` — Docker bind-mounts retain the old inode otherwise):

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  restart nginx
```

### Database connection errors

```bash
# Check if postgres is running
docker compose exec postgres pg_isready -U xpuser

# Verify the connection from the API container
docker compose exec api python -c "
from sqlalchemy import create_engine, text
e = create_engine('postgresql://xpuser:<password>@postgres:5432/ipsolis')
with e.connect() as c: print(c.execute(text('SELECT 1')).scalar())
"
```

### AD / LDAP connection issues

1. Verify network connectivity from the container:
   ```bash
   docker compose exec api curl -v telnet://dc01.yourcompany.com:389
   ```
2. Check the AD settings in Admin > Settings
3. Review API logs for LDAP errors:
   ```bash
   docker compose logs api 2>&1 | grep -i "ldap\|ad_lookup"
   ```

### Emails not sending

1. Verify SMTP settings in Admin > Settings
2. Check worker logs for SMTP errors:
   ```bash
   docker compose logs worker 2>&1 | grep -i "smtp\|mail\|notification"
   ```
3. Ensure the server can reach the SMTP relay:
   ```bash
   docker compose exec api curl -v telnet://smtp.yourcompany.com:587
   ```

### Permission denied on certs directory

```bash
sudo chmod 644 certs/cert.pem
sudo chmod 600 certs/key.pem
```

---

## 14. Clean Reset (Test Environments)

> **Test and staging environments only.** This section permanently destroys all
> data. Never run on a production instance.

Docker volumes (database data, Redis data) survive `rm -rf /opt/ipsolis` because
they are stored under `/var/lib/docker/volumes/` — independent of the repository
directory. For a fully clean reinstall:

```bash
# 1. Stop the stack and delete volumes
cd /opt/ipsolis
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  down -v

# 2. Remove the repository directory
cd /opt
sudo rm -rf ipsolis

# 3. Reinstall (continue from section 2)
sudo git clone https://github.com/XenPool/ipsolis.git ipsolis
cd ipsolis
```

After this reset the database contains no users, no configuration and no assets —
the initial setup (section 7) must be completed again.
