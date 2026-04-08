# XenPool IT Selfservice

A production-ready platform for orchestrating IT asset lifecycle workflows — VDIs today, any asset type tomorrow. Provides a self-service portal for end users and a webhook receiver for ServiceNow integration.

## Features

- **Self-Service Portal** — Entra ID SSO (MSAL), users can order, extend, and cancel assets
- **Admin UI** — manage asset types, runbooks, PS modules, app config, and audit logs
- **Dynamic Runbook Engine** — configurable per asset type and action (`provision`, `delete`, `modify`, `extend`)
- **Flexible Target Execution** — manage AD / Active Roles group memberships as part of any workflow
- **Pool Capacity Enforcement** — pre-flight checks with HTTP 409 on overcommit
- **Celery Beat Scheduler** — automated expiry checks and lifecycle management
- **XCP-ng / XenServer + vSphere** — PowerShell/PowerCLI scripts for VM operations
- **Active Roles / WinRM** — group and account management via pypsrp
- **SCCM** — unattended reinstall task sequence integration
- **ServiceNow Webhook** — inbound order dispatch via `/webhook`

## Stack

| Layer | Technology |
|---|---|
| API / Dispatcher | FastAPI (Python 3.12) |
| Workflow Engine | Celery + Redis |
| Scheduling | Celery Beat |
| Database | PostgreSQL (SQLAlchemy + Alembic) |
| Frontend | HTMX + Jinja2 + Tailwind CSS |
| Auth | Entra ID SSO (MSAL) |
| VM Operations | PowerShell + PowerCLI (XenServer/XCP-ng, vSphere) |
| Directory | Active Roles via WinRM (pypsrp) |
| Deployment | Docker Compose |

## Quickstart

```bash
cp .env.example .env
# Edit .env — set passwords, secrets, ENVIRONMENT=development for mock mode
docker compose up --build
```

| Service | URL |
|---|---|
| API + Admin UI | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |
| Celery Flower | http://localhost:5555 |
| Self-Service Portal | http://localhost:8000/portal |

## Development

**Mock mode** — set `ENVIRONMENT=development` in `.env` to mock all external calls (vSphere, Active Roles, SCCM, SMTP) with realistic delays and logging. No external infrastructure required.

**Run tests:**
```bash
docker compose exec api python -m pytest tests/ -v
```

**Database migrations:**
```bash
# Create a new migration
docker compose exec api alembic revision --autogenerate -m "description"

# Apply migrations
docker compose exec api alembic upgrade head
```

> Note: migration files are embedded at image build time. For a running container use `docker cp` + `alembic upgrade head` directly.

## Project Structure

```
api/
  app/
    models/         ORM models
    routes/         FastAPI routers (admin, portal, auth, webhook, …)
    schemas/        Pydantic schemas
    templates/      Jinja2 templates (Admin UI + Portal)
    utils/          Auth, capacity, asset type constraints, …
  alembic/          Database migrations
  tests/            Pytest suite (happy-path coverage)
worker/
  tasks/
    modules/        Atomic workflow modules (pool, active_roles, vsphere, …)
    workflows/      Celery workflow tasks (dynamic_runner)
scripts/
  xenserver/        XCP-ng / XenServer PowerShell scripts
  vsphere/          vSphere PowerShell scripts
  active_roles/     Active Roles PowerShell scripts
  sccm/             SCCM task sequence scripts
```

## Key Conventions

- **Audit logging** — `aaudit()` (async, API) · `waudit()` (sync, Worker)
- **Step tracking** — `worker/tasks/modules/step_helper.py`
- **Admin auth** — dev bypass active; production: set `ADMIN_API_KEY` in `.env`
- **Jinja2 + JS** — use `'{{' + var + '}}'` string concatenation instead of template literals to avoid Jinja2 conflicts
- **Scripts** — all scripts in `scripts/` return JSON on stdout; PowerShell scripts must be pure ASCII

## Documentation

Full project context and architecture decisions: [CLAUDE.md](CLAUDE.md)
Task backlog: [TASKS.md](TASKS.md)
