# XenPool IT Selfservice – Projektkontext für Claude Code

## Task-Backlog
Offene und abgeschlossene Tasks: siehe [`TASKS.md`](TASKS.md)
Bitte zu Sessionbeginn lesen und bei Abschluss eines Tasks aktualisieren.

## Projektziel

Eigenständiger, produktreifer Ersatz für **Ivanti Automation** zur Orchestrierung
von IT-Asset-Lifecycle-Prozessen (VDIs heute, beliebige Assets morgen).
Bringt eigenes Self-Service-Portal mit, kann aber auch ServiceNow-Webhooks empfangen.

## Stack

| Schicht | Technologie |
|---|---|
| API / Dispatcher | FastAPI (Python 3.12) |
| Workflow Engine | Celery + Redis |
| Scheduling | Celery Beat |
| Datenbank | PostgreSQL (via SQLAlchemy + Alembic) |
| Externe Systeme | vSphere (PowerCLI), Active Roles (WinRM/pypsrp), SCCM, SMTP |
| Container | Docker / Docker Compose |
| Frontend | HTMX + Tailwind CSS |

## Branch-Strategie

- `main` – stabiler Stand / Produktion
- `dev` – aktive Entwicklung (alle PRs hierhin)
- Feature-Branches nach Bedarf: `feature/<name>`
- Merges nach `main` nur bei stabilem, getesteten Stand

## Lokal starten

```bash
cp .env.example .env
# .env anpassen (Passwörter, Secrets etc.)
docker compose up --build
```

API läuft auf http://localhost:8000 · Celery Flower auf http://localhost:5555

## Entwicklungshinweise

### Mock-Modus
Alle externen Aufrufe (vSphere, Active Roles, SCCM, SMTP) sind gemockt wenn
`ENVIRONMENT=development` in der `.env` gesetzt ist. Mocks simulieren realistisches
Verhalten inkl. Laufzeiten und Logging.

### PowerShell Scripts
**Die Scripts in `scripts/ivanti/` werden NICHT verändert.** Sie sind die originalen
Ivanti-Module und dienen als Vorlage/Referenz für neue Scripts in `scripts/vsphere/`
und `scripts/active_roles/`.

### Datenbankmigrationen
```bash
# Neue Migration erstellen
docker compose exec api alembic revision --autogenerate -m "beschreibung"

# Migrationen anwenden
docker compose exec api alembic upgrade head
```

**Hinweis:** Alembic-Migrationsdateien werden beim Image-Build eingebettet.
Bei laufendem Container: `docker cp` + `alembic upgrade head` direkt im Container.
Enum-Typen (z.B. `order_action`) bereits vorhanden → `op.execute(raw SQL)` statt
`op.create_table()` mit `sa.Enum`, um `DuplicateObject`-Fehler zu vermeiden.

### Jinja2 in Templates
JS-Template-Literals mit `{{` / `}}` kollidieren mit Jinja2-Syntax.
Statt `` `{{${p}}}` `` immer `'{{' + p + '}}'` (String-Konkatenation) verwenden.

## Wichtige Dateipfade

| Pfad | Beschreibung |
|------|-------------|
| `api/app/main.py` | FastAPI-Einstiegspunkt, Router-Registrierung |
| `api/app/config.py` | Pydantic Settings (Env-Variablen) |
| `api/app/database.py` | SQLAlchemy Engine + Session |
| `api/app/models/` | ORM-Models |
| `api/app/routes/` | API-Routen |
| `api/app/templates/` | Jinja2-Templates (HTMX-UI + Portal) |
| `api/app/utils/module_registry.py` | Modul-Metadaten-Spiegel für Admin-UI |
| `worker/tasks/__init__.py` | Celery App-Instanz |
| `worker/tasks/workflows/` | Runbook-Workflows (Celery Tasks) |
| `worker/tasks/modules/` | Atomare Module (pool, active_roles, vsphere, …) |
| `scripts/ivanti/` | Referenz-Scripts (read-only) |
| `scripts/vsphere/` | Editierbare vSphere-Scripts |
| `scripts/active_roles/` | Editierbare Active-Roles-Scripts |

## Konzeptionelle Entsprechungen Ivanti → XenPool

| Ivanti | XenPool IT Selfservice |
|---|---|
| Modul | `worker/tasks/modules/*.py` |
| Runbook | DB-Tabelle `runbook_definitions` + `runbook_steps` (dynamic_runner) |
| Variablenverwaltung | `app_config`-Tabelle + `.env` |
| Dispatcher | FastAPI `/webhook` oder `/orders` |
| Audit-Log | `audit_log`-Tabelle (unveränderlich) |

## Externe Systemanbindungen

- **vSphere**: PowerCLI-Scripts via `subprocess` (pwsh in Worker-Container)
- **Active Roles**: pypsrp / WinRM → Windows-Host mit Active Roles Console
- **SCCM**: WinRM-Aufruf für Unattended Reinstall-Tasksequenz
- **SMTP**: Python `smtplib` für Benachrichtigungen

## Datenbankschema (Überblick)

| Tabelle | Beschreibung |
|---------|-------------|
| `asset_types` | Typdefinitionen inkl. `asset_model` (named/pooled), `pool_capacity` |
| `asset_pool` | Alle verwalteten VMs/Assets |
| `orders` | Bestellungen und Änderungsaufträge |
| `order_steps` | Einzelne Modul-Schritte je Bestellung (mit structured JSON log) |
| `runbook_definitions` | Ein Runbook pro Asset-Typ + Action |
| `runbook_steps` | Geordnete Modul-Aufrufe je Runbook |
| `audit_log` | Unveränderliches Protokoll |
| `app_config` | Zentrale Konfigurationsvariablen |
