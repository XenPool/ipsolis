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
cp .env.example .env
nano .env
```

### Required settings to change

```ini
# Secure database credentials
POSTGRES_PASSWORD=<generate-a-strong-password>

# Secure API secrets -- use random strings of 32+ characters
API_SECRET_KEY=<random-string-min-32-chars>
WEBHOOK_SECRET_TOKEN=<random-string>
ADMIN_API_KEY=<random-string-min-32-chars>

# CORS -- set to your production domain
CORS_ORIGINS=https://selfservice.yourcompany.com
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

# Generate the certificate for your hostname
sudo mkdir -p certs
sudo mkcert -cert-file certs/cert.pem -key-file certs/key.pem selfservice.yourcompany.com
```

> **Important**: For browsers on other machines to trust this certificate, you must
> distribute the root CA (`mkcert -CAROOT` shows the path) to client machines via
> Group Policy or your enterprise CA trust store.

### Option B: Certificate from your Enterprise CA (Recommended for production)

If your organization runs an internal Certificate Authority (e.g., Active Directory Certificate Services):

1. Generate a CSR on the server:
   ```bash
   sudo mkdir -p certs
   sudo openssl req -new -newkey rsa:2048 -nodes \
     -keyout certs/key.pem \
     -out certs/server.csr \
     -subj "/CN=selfservice.yourcompany.com"
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
sudo certbot certonly --standalone -d selfservice.yourcompany.com

# Symlink into the certs directory
sudo mkdir -p certs
sudo ln -sf /etc/letsencrypt/live/selfservice.yourcompany.com/fullchain.pem certs/cert.pem
sudo ln -sf /etc/letsencrypt/live/selfservice.yourcompany.com/privkey.pem certs/key.pem
```

#### Set up auto-renewal (Option C only)

```bash
# Test renewal
sudo certbot renew --dry-run

# Add a cron job to reload nginx after renewal
echo "0 3 * * * certbot renew --quiet --post-hook 'docker exec ipsolis-nginx nginx -s reload'" | sudo crontab -
```

### Configure nginx

The repository already ships a ready-to-use `nginx/nginx.conf` with the placeholder `YOUR_HOSTNAME`. Replace both occurrences of the placeholder with your actual hostname (`sed` with the `g` flag handles both in one pass):

```bash
sudo sed -i 's/YOUR_HOSTNAME/selfservice.yourcompany.com/g' nginx/nginx.conf
```

The file will look like this afterwards (for reference):

```nginx
server {
    listen 80;
    server_name selfservice.yourcompany.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name selfservice.yourcompany.com;

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
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

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
curl -f http://localhost:8000/health

# Through nginx (HTTPS)
curl -fsk https://selfservice.yourcompany.com/health
```

---

## 7. Initial Admin Setup

### First-run admin account (RBAC)

Open **https://selfservice.yourcompany.com/ui/** in your browser. On
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
   `https://selfservice.yourcompany.com/ui/license`).
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

Navigate to **Admin > Settings** and configure the following:

#### Active Directory (Required)

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

#### SMTP (Required for notifications)

| Setting | Description | Example |
|---------|-------------|---------|
| `smtp.host` | SMTP relay hostname | `smtp.yourcompany.com` |
| `smtp.port` | SMTP port | `587` |
| `smtp.user` | SMTP username (if auth required) | `selfservice@yourcompany.com` |
| `smtp.password` | SMTP password | *(marked as secret)* |
| `smtp.tls` | Use STARTTLS | `true` |
| `smtp.from` | Sender email address | `noreply@yourcompany.com` |
| `smtp.from_name` | Sender display name | `ip·Solis` |

#### Email Templates

Navigate to **Admin > Email Templates** to customize notification emails.
Default templates are created during migration. You can edit the subject line
and body using `{{variable}}` placeholders.

#### Portal Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `portal.max_advance_days` | How far ahead users can schedule orders | `0` (unlimited) |
| `portal.app_title` | Application title shown in the portal | `ip·Solis` |

### Create your first Asset Type

1. Go to **Admin > Asset Types > New**
2. Fill in the name, description, and category
3. Configure the automation strategy (Group Access, Runbook, or Composite)
4. Set approval requirements if needed
5. Optionally restrict access with an Eligible Requestors group DN
6. Save

### Create Runbooks (if applicable)

ip·Solis ships with a fully configured example runbook:
**"Virtual Machine Recycler"** — a standalone runbook that includes all required
script modules (XenServer/XCP-ng, SCCM, Active Directory) and can serve as a
template for your own automation.

Find it under **Admin > Standalone Runbooks** to inspect, copy, or adapt it.

To create asset-type runbooks:

1. Go to **Admin > Runbooks > New**
2. Define the steps (PowerShell modules or built-in modules)
3. Link the runbook to an asset type

Any number of custom runbooks with any combination of steps can be created — there
is no restriction to specific modules or templates.

---

## 8. Entra ID SSO (Portal Authentication)

The self-service portal supports Microsoft Entra ID (Azure AD) for single sign-on.

### Register an App in Entra ID

1. Go to the [Azure Portal](https://portal.azure.com) > **App registrations** > **New registration**
2. Name: `ip·Solis`
3. Redirect URI: `https://selfservice.yourcompany.com/portal/auth/callback` (Web)
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
| `entra.redirect_uri` | `https://selfservice.yourcompany.com/portal/auth/callback` |
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

- [ ] **HTTPS**: `https://selfservice.yourcompany.com` loads with a valid certificate
- [ ] **Admin UI**: `https://selfservice.yourcompany.com/ui/` is accessible
- [ ] **First-run setup**: visiting the admin login renders the "Create first administrator" form (or, if already done, the regular sign-in form with no error)
- [ ] **Setup checklist**: the dashboard shows the in-app setup checklist; tick off Essential items as you configure them
- [ ] **Portal login**: Users can sign in via Entra ID SSO
- [ ] **AD lookup**: On the order form, user validation (deputy, RDP, admin fields) resolves names
- [ ] **Email**: Submit a test order and confirm notification email arrives
- [ ] **Health check**: `curl -fsk https://selfservice.yourcompany.com/health` returns `{"status": "ok"}`
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
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

# Run any new database migrations
docker compose exec -T api alembic upgrade head

# Reload nginx to pick up new container IPs
docker compose exec -T nginx nginx -s reload

# Verify health
curl -fsk https://selfservice.yourcompany.com/health
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

ip·Solis is built to scale horizontally on every layer except Postgres
(single-writer by design). The Beat scheduler supports multi-replica HA
via celery-redbeat, and this section covers the remaining three layers: API replicas
behind a load balancer, worker replicas per Celery queue, and a
Postgres read-replica + failover plan.

> **Status note**: the patterns in this section have been verified
> against single-host stacks and the codebase's stateless contracts
> (cookie-signed sessions, RedBeat-locked Beat, queue-routed Celery).
> The Postgres standby + failover plan **needs real failover testing
> in a staging environment before production roll-out** — the docs
> are accurate but the operational runbook (read-replica promotion,
> connection-string flip, Celery worker reconnect) hasn't been
> drilled end-to-end on the project's own stack. Treat the Postgres
> guidance as a reference architecture rather than a battle-tested
> playbook.

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
  curl -fsk https://selfservice.yourcompany.com/health \
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
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --scale beat=2
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

### 12.3 Postgres standby + failover

> **Read this twice**: ip·Solis is single-primary against Postgres.
> A standby is for **disaster recovery / read scale-out**, not for
> active-active writes. Promoting a standby is a manual operation
> (or scripted via Patroni / repmgr / pg_auto_failover); the
> application's connection string must be flipped to point at the
> new primary, and every API + worker + Beat replica restarted to
> drop stale connections from the asyncpg / psycopg2 pools.

**Two complementary tools**:

| Tool | What it does | Where it fits |
|---|---|---|
| **Streaming replication** (built into Postgres) | Continuous WAL stream from primary → standby. Standby is read-only and lags behind primary by 10ms–seconds depending on load. | Daily operations: hot read replica, near-zero RPO failover candidate. |
| **pgBackRest** | Backup + PITR + standby bootstrap. Stores compressed encrypted backups in object storage (S3 / Azure Blob / on-prem object store). | Disaster recovery: cold backup, can restore to any point in time within retention, used to bootstrap fresh standbys without touching the primary. |

Production deployments typically use **both**: pgBackRest for
backups + standby bootstrap, streaming replication for the live
standby. The patterns below assume that combination.

#### 12.3.1 Streaming replication setup

On the **primary** (`ipsolis-postgres` container — bind-mount the config
overlay so it survives image rebuilds):

```ini
# postgresql.conf overlay (mount as /etc/postgresql/conf.d/replication.conf)
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
synchronous_commit = on   # async-only ('off') saves a few ms per write
                          # at the cost of unbounded lag on the standby —
                          # leave at 'on' unless you have a dedicated
                          # WAL-relay replica and accept the trade-off.
```

```ini
# pg_hba.conf — allow the standby host to authenticate as a replication user
# (CIDR matches your standby's network)
host  replication  ipsolis_repl  10.0.0.0/24  scram-sha-256
```

Create the replication user once (on the primary):

```sql
CREATE ROLE ipsolis_repl WITH REPLICATION LOGIN PASSWORD '<rotate-me>';
SELECT pg_create_physical_replication_slot('ipsolis_standby_1');
```

On the **standby** host (separate VM / container, not `ipsolis-postgres`):

```bash
# Bootstrap the standby's data dir from the primary
pg_basebackup \
  -h <primary_host> -U ipsolis_repl -W \
  -D /var/lib/postgresql/data \
  -X stream -R --slot=ipsolis_standby_1 \
  -P
```

`-R` writes `standby.signal` + connection info into
`postgresql.auto.conf` so the standby starts in hot-standby mode
on next boot. Restart the standby's Postgres process and verify:

```sql
-- On the standby
SELECT pg_is_in_recovery();           -- → t
SELECT now() - pg_last_xact_replay_timestamp();  -- replication lag
```

#### 12.3.2 pgBackRest backup + bootstrap

```ini
# pgbackrest.conf on the primary
[global]
repo1-type=s3
repo1-s3-bucket=ipsolis-backups
repo1-s3-region=eu-central-1
repo1-s3-key=AKIA…
repo1-s3-key-secret=…
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=<rotate-me>
repo1-retention-full=14
repo1-retention-diff=7

[ipsolis]
pg1-path=/var/lib/postgresql/data
```

Daily full backup + hourly differentials via cron / systemd timer:

```bash
# Weekly full
pgbackrest --stanza=ipsolis backup --type=full

# Daily incremental + WAL archive
pgbackrest --stanza=ipsolis backup --type=incr
```

Restore (PITR) for DR:

```bash
pgbackrest --stanza=ipsolis --type=time \
  --target='2026-04-30 14:30:00+02' \
  restore
```

This is also how a fresh standby is bootstrapped without touching
the primary — `pgbackrest restore` to the standby's data dir
(replacing the `pg_basebackup` step above), then start Postgres
in standby mode with the same `standby.signal` + replication slot
config.

#### 12.3.3 Failover plan

Manual failover (no Patroni / repmgr — keep it simple):

1. **Verify the standby is current** —
   `SELECT now() - pg_last_xact_replay_timestamp()` should be < 1s
   under typical load. Anything over that means in-flight
   transactions might be lost.
2. **Stop writes** — bring the API + worker + Beat replicas down
   so nothing is hammering the primary while the cutover happens.
3. **Promote the standby** — on the standby host:

   ```bash
   pg_ctl promote -D /var/lib/postgresql/data
   ```

   The standby exits recovery mode and becomes a read-write primary.
4. **Flip the connection string** — update `DATABASE_URL` in `.env`
   to point at the new primary. All replicas must be updated; on a
   single-host stack this is one file edit.
5. **Restart everything**:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     restart api worker beat
   ```

   The old asyncpg / psycopg2 pools drop stale connections during
   restart; new pools authenticate against the freshly-promoted
   primary.
6. **Rebuild the dead primary as a new standby** — once the failed
   primary is recovered, run the standby bootstrap (12.3.1) against
   the *new* primary so the topology has a hot replica again.

**RPO / RTO realistic targets**:

| Metric | Streaming replication only | + pgBackRest |
|---|---|---|
| Recovery Point Objective (data loss) | ≤ 1s under normal load | Same (streaming replication is the live data path) |
| Recovery Time Objective (downtime) | 5–15 minutes (manual promotion + restart) | Same — pgBackRest doesn't accelerate live failover; it accelerates *cold* recovery from a deleted DB |

**Automation**: the manual flip is fine for stacks where 5–15
minutes of downtime per year is acceptable. Patroni
(<https://patroni.readthedocs.io/>) automates the
quorum/promotion/connection-string cutover and can drop RTO to
under a minute, at the cost of a Consul / etcd / Zookeeper
control plane to run alongside Postgres.

#### 12.3.4 Verification before going live

Treat Postgres HA as **untested until you've drilled it on staging**:

1. Bootstrap the standby from a primary copy of staging data.
2. Verify `pg_is_in_recovery()` returns `t` and replication lag
   sits under 1s under simulated load.
3. Stop the primary container; promote the standby; flip
   `DATABASE_URL`; restart the api/worker/beat replicas.
4. Verify the API answers `/health` against the new primary,
   create a test order through the portal, observe it land in the
   new primary's `orders` table.
5. Rebuild the dead primary as a new standby and re-run the lag
   check.

**Until that drill has been done end-to-end on your stack**, the
HA story is "we have backups + a read replica" — not "we have
verified failover." Document the difference in your DR plan.

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

Nginx may have cached the old container IP. Reload it:

```bash
docker compose exec -T nginx nginx -s reload
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
