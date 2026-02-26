# XenPool IT Selfservice – Task-Backlog

Format: `[offen]` / `[erledigt]` / `[blockiert]`
Neue Tasks oben eintragen. Erledigte bleiben als Referenz.

---

## Offen

### [erledigt] Phase 1: Deprovision Policy + Personal Provisioning Strategy — 2026-02-25
**DB (`0008_deprovision_policy_and_provisioning_strategy.py`)**
- [x] Neue Spalten `asset_types`: `deprovision_policy`, `personal_provisioning_strategy`, `naming_pattern`, `max_per_user`
- [x] Datenmigration: capacity_pooled → return_to_pool, dedicated_shared → access_only, assigned_personal → deallocate_instance
- [x] Neue Enums `DeprovisionPolicy` (5 Werte) + `PersonalProvisioningStrategy` (3 Werte) in `models/asset.py`

**Backend**
- [x] `schemas/admin.py` + `schemas/asset.py`: neue Felder in AssetTypeCreate/Update/Read
- [x] `routes/admin.py`: create/update-Handler + `_type_snap()` in `utils/audit.py` erweitert
- [x] `dynamic_runner.py`: Revoke-Routing nach `deprovision_policy` (5 Pfade inkl. Stubs für deallocate/delete)
- [x] `pool_manager.py`: `reserve_asset()` berücksichtigt `personal_provisioning_strategy` (ASSIGN_EXISTING_FREE / REUSE_BY_OWNER / CREATE_NEW-Stub)

**Admin-UI (`asset_type_form.html`)**
- [x] Deprovision Policy Radio-Gruppe (5 Optionen, orange) nach Zuweisungsmodell
- [x] Persönliche Zuweisung Section (nur sichtbar bei assigned_personal): Strategy-Radio + Naming Pattern + Max per User
- [x] JS: `toggleAssignmentDependents()` setzt automatisch sinnvollen Deprovision-Policy-Default

**Verifikation**
- [x] Migration 0008 auf laufendem Container angewendet (`alembic upgrade head`)
- [x] `POST /admin/asset-types` → neue Felder werden korrekt gespeichert und zurückgegeben
- [x] DB-Check: Datenmigration capacity_pooled → return_to_pool ✓

---

### [erledigt] Phase 2: Automation Strategy COMPOSITE — 2026-02-26
Erweitert `automation_mode` (2 Werte) auf `automation_strategy` (3 Werte) inkl. COMPOSITE-Modus,
bei dem Gruppen-Targets und Runbook in konfigurierbarer Reihenfolge laufen.

**DB (`0009_automation_strategy_composite.py`)**
- [x] Neue Spalten `asset_types`: `automation_strategy` (VARCHAR 20, DEFAULT 'runbook_only'), `composite_steps` (JSONB, nullable)
- [x] Datenmigration: targets_only → group_only, runbook → runbook_only; `automation_mode` bleibt als deprecated-Fallback

**Backend**
- [x] Neues Enum `AutomationStrategy` (GROUP_ONLY / RUNBOOK_ONLY / COMPOSITE) in `models/asset.py`
- [x] `schemas/admin.py` + `schemas/asset.py`: neue Felder
- [x] `dynamic_runner.py`: Mode-Routing mit Fallback auf `automation_mode`; `_run_composite_mode()` + `_run_runbook_path()` extrahiert

**Admin-UI**
- [x] 3-Karten-Toggle (GROUP_ONLY / RUNBOOK_ONLY / COMPOSITE) ersetzt bisherigen 2-Karten-Toggle
- [x] Bei COMPOSITE: Reihenfolge-Radio (Gruppen zuerst vs. Runbook zuerst) + `composite_steps` schreiben

**Verifikation**
- [x] Migration 0009 auf laufendem Container angewendet (0008 → 0009)
- [x] Datenmigration: targets_only → group_only, runbook → runbook_only ✓
- [x] `POST /admin/asset-types` mit `automation_strategy=composite` → 201, `composite_steps` korrekt gespeichert
- [x] Worker: `_run_composite_mode`, `_run_runbook_path` importierbar

---

### [erledigt] Phase 3: Typisiertes Attribut-Modell + Portal-Rendering — 2026-02-26
Erweitert `config` JSONB um Typ-System (STRING/INT/BOOL/ENUM/MULTI_ENUM), Validierung und
visibleWhen-Logik. Portal rendert Bestellformular dynamisch nach Attribut-Definition.

**Kein DB-Schema-Change** (JSONB-Format rückwärtskompatibel erweitert)

**Backend**
- [x] Neues Pydantic-Schema `AttributeDefinition` + `AttributeType` Enum in `schemas/admin.py`
- [x] Server-seitige Validierung `_validate_order_attrs()` in `routes/portal.py`: Pflichtfelder, Typ-Konvertierung (INT/BOOL/ENUM/MULTI_ENUM), ENUM-Wert in options, visibleWhen-Logik
- [x] `Order.config` (JSONB) wird mit validierten Attributwerten befüllt

**Admin-UI (`asset_type_form.html`)**
- [x] Attribut-Editor: Typ-Dropdown (STRING/INT/BOOL/ENUM/MULTI_ENUM) + Pflichtfeld-Checkbox + Standardwert
- [x] Optionen-Zeile erscheint/verschwindet per JS basierend auf Typ (`updateAttrTypeUI()`)
- [x] `submitForm()` sammelt type, required, default_value, options pro Attribut

**Portal (`bestellung_neu.html`)**
- [x] Pre-rendered Attribut-Sektionen je Asset-Typ: text / number / checkbox / select / Mehrfach-Checkbox
- [x] JS `updateAttrSection()`: zeigt/versteckt Abschnitt bei Asset-Typ-Wechsel
- [x] JS `applyVisibleWhen()`: `data-visible-when-field/value` → dynamisches Ein-/Ausblenden
- [x] Submit-Handler: versteckte Inputs werden deaktiviert (nicht mit abgeschickt)

**Verifikation**
- [x] `AttributeDefinition` Validierung: ENUM ohne options → Fehler; required missing → 422; visibleWhen skip ✓
- [x] Portal: `GET /portal/bestellung/neu` 200 OK; `attr-section-wrapper`, `applyVisibleWhen`, `updateAttrSection` in HTML ✓
- [x] POST ohne Pflichtfeld → 422 + Fehlermeldung "Pflichtfeld 'Anzahl CPUs' wurde nicht ausgefüllt." ✓
- [x] POST mit gültigem Attribut → 303 Redirect; Order `config: {'cpu': '4'}` in DB gespeichert ✓

---

### [offen] Phase 4: Order State Persistence + Deterministic Revoke — Prio 4
Persistiert nach erfolgreicher Provision einen Snapshot auf der Order (provisioned_state JSONB).
Revoke liest ausschließlich aus diesem Snapshot — deterministisch auch wenn Asset-Typ geändert wurde.
Idempotenz für Gruppen-Grants via eindeutigem Key.

**DB (`0010_order_provisioned_state.py`)**
- [ ] Neue Spalte `orders.provisioned_state` JSONB
- [ ] Neue Spalten `order_change_log`: `idempotency_key` VARCHAR(255), `resolved_object_id` VARCHAR(255), Index auf idempotency_key
- [ ] `OrderStatus`-Enum erweitern: PROVISIONING / PROVISIONED / REVOKING / REVOKED (via `ALTER TYPE ... ADD VALUE IF NOT EXISTS`)
- [ ] Status-Badge-Updates in Portal: DELIVERED | PROVISIONED = aktiv; REVOKED = abgeschlossen

**Backend**
- [ ] `models/order.py`: `provisioned_state` Spalte + neue OrderStatus-Werte
- [ ] `models/change_log.py`: idempotency_key + resolved_object_id
- [ ] `dynamic_runner.py`: nach Provision `provisioned_state` schreiben; Revoke liest deprovision_policy aus Snapshot (Fallback auf current config)
- [ ] `target_executor.py`: idempotency_key generieren + Duplikat-Check vor Grant; resolved_object_id schreiben
- [ ] `pool_manager.py`: `asset_metadata` mit owner_email anreichern (→ REUSE_BY_OWNER-Lookup)
- [ ] `check_expiring_assets` in `vdi_reclaim.py`: auch PROVISIONED-Status berücksichtigen

---

### [offen] Beat-Scheduler → dynamic_runner migrieren — Prio 5
Der stündliche Ablauf-/Reclaim-Task (`check_expiring_assets`) ruft noch den hardcodierten
`vdi_reclaim`-Workflow auf. Muss auf `dynamic_runner` umgestellt werden, damit der
Lifecycle-Abschluss ebenfalls DB-gesteuert läuft.
- [ ] `worker/tasks/workflows/vdi_reclaim.py`: `check_expiring_assets` auf `dynamic_runner.run` umstellen
- [ ] Sicherstellen dass `delete`-Runbook für betroffene Asset-Types definiert ist

### [offen] Portal-Authentifizierung — Prio 6
Portal ist aktuell vollständig offen (nur E-Mail-Eingabe, keine Session/Auth).
Für produktiven Einsatz im Firmennetz mindestens eine der folgenden Optionen:
- [ ] Option A: Entra ID / OIDC (SSO via `msal` oder `authlib`)
- [ ] Option B: Einfache IP-Allowlist + Session-Cookie (schneller für internes MVP)
Entscheidung steht noch aus.

### [offen] Admin-UI: Asset Pool Management — Prio 7
Admins können VMs aktuell nur über die API (`POST /admin/assets`) dem Pool hinzufügen.
Keine HTML-Oberfläche vorhanden.
- [ ] Tabellen-Ansicht aller Assets im Pool (`/ui/assets`)
- [ ] Formular: Asset hinzufügen (Name, Asset-Type, Hostname/IP)
- [ ] Asset deaktivieren / aus Pool entfernen

### [offen] Docker-Image neu bauen (Prod-Readiness) — Prio 8
Aktuelle Änderungen laufen nur via Volume-Mounts. Für stabilen Deploy:
- [ ] `docker compose up --build` durchführen und verifizieren
- [ ] `.env.example` auf neue Variablen prüfen/ergänzen

### [offen] Basis-Tests (Happy Path) — Prio 9
Kein einziger automatisierter Test vorhanden.
- [ ] pytest-Setup in `api/tests/`
- [ ] Happy-Path: Order erstellen → dynamic_runner läuft durch → Status = delivered
- [ ] Runbook-Lookup: korrektes Runbook für Asset-Type + Action gefunden

---

## Erledigt

### [erledigt] Asset Contract Model — Assignment Model, Targets, Change Log, Self-Service — 2026-02-25
Vollständige Neugestaltung des Asset-Modells: Assignment-Model (3 Werte), Config-driven Automation,
deterministisches Change-Log, User Self-Service Abbestellen.

**Phase 1 – DB Schema + Python Models**
- [x] Migration `0007_assignment_model_and_targets.py`: neue Spalten in `asset_types` (`assignment_model`, `targets`, `automation_mode`, `lifecycle_ttl_days`, `lifecycle_renewable`), neue Tabelle `order_change_log`
- [x] Enum `AssignmentModel` (capacity_pooled, dedicated_shared, assigned_personal) in `models/asset.py`
- [x] Neues ORM-Model `api/app/models/change_log.py`: `OrderChangeLog`
- [x] Schemas `admin.py` + `asset.py` um alle neuen Felder erweitert
- [x] `admin.py` Create/Update-Handler um neue Felder erweitert
- [x] Bugfix: `lifecycle_renewable` als `Boolean` (statt `Integer`) im ORM — asyncpg-Kompatibilität

**Phase 2 – Admin UI**
- [x] `asset_type_form.html`: 3 Assignment-Model-Karten, Automation-Mode-Toggle, Targets-Editor, Lifecycle-Abschnitt
- [x] `asset_types.html`: Spalte "Modell" → "Zuweisungsmodell" (3 Badge-Werte), neue Spalte "Automation"

**Phase 3 – Target Executor + Dynamic Runner**
- [x] Neues Modul `worker/tasks/modules/target_executor.py`: `grant()` + `revoke()` (config-driven Gruppen-Zugriff, deterministisches Change-Log)
- [x] `registry.py`: `target_executor.grant` + `target_executor.revoke` registriert
- [x] `api/app/utils/module_registry.py`: Mirror-Metadaten für Admin-UI
- [x] `dynamic_runner.py`: Mode-Split (`targets_only` vs `runbook`), `_run_targets_mode()`, `_run_step_inline()` Helper
- [x] Bugfix: `dynamic_runner` in `tasks/__init__.py` `include=[]` + `task_routes` eingetragen

**Phase 4 – User Self-Service Portal**
- [x] `portal.py`: `POST /portal/bestellungen/{order_id}/cancel` Endpoint
- [x] `bestellung_detail.html`: Abbestellen-Karte (Details/Summary inline confirm), Labels "VM" → "Zugang"
- [x] `portal/index.html`: "Neue VDI bestellen" → "Neuen Zugang beantragen", "VM-Typ" → "Asset-Typ"
- [x] `bestellung_neu.html`: Titel auf "Neuen Zugang beantragen" aktualisiert

**Verifikation**
- [x] Migration 0007 auf laufendem Container via `docker cp` + `alembic upgrade head` angewendet
- [x] End-to-End: `targets_only` Asset-Typ erstellt → Provision-Order → DELIVERED → `order_change_log` state=success → Cancel-Order → DELIVERED → state=rolled_back

---

### [erledigt] Dynamische Runbooks & Admin-UI (Option B) — 2026-02-24/25
Vollständige Implementierung des DB-gesteuerten Runbook-Systems.

**Backend**
- [x] A1 – Migration `0005_runbook_tables.py`: Tabellen `runbook_definitions`, `runbook_steps`, Asset-Types um `asset_model`/`pool_capacity` erweitert; Seed-Runbooks für Test VDI & Business VDI
- [x] A2 – ORM-Models `api/app/models/runbook.py`: `RunbookDefinition`, `RunbookStep`
- [x] A3 – Worker Module-Registry `worker/tasks/modules/registry.py`
- [x] A4 – API Module-Registry (Metadaten-Spiegel) `api/app/utils/module_registry.py`
- [x] A5 – `pool_manager.py`: `check_capacity()` hinzugefügt
- [x] A6 – `worker/tasks/workflows/dynamic_runner.py`: dynamischer Workflow-Executor + `test_module_run` Task
- [x] A7 – `step_helper.py`: Structured JSON Logging (`make_log_json`)
- [x] A8 – `webhook.py`: Dispatch auf `dynamic_runner`

**Admin-UI**
- [x] B1 – `api/app/routes/admin_runbooks.py`: CRUD für Asset-Types, Runbooks, Steps, Modul-Metadaten
- [x] B2 – `api/app/routes/ui.py`: neue UI-Routen (asset-types, runbooks, scripts, HTMX-Fragmente)
- [x] B3 – Templates: `asset_types.html`, `asset_type_form.html`, `runbooks.html`, `runbook_editor.html`

**Script-Editor**
- [x] C1 – `docker-compose.yml`: `./scripts:/app/scripts` Volume in api-Service
- [x] C2 – `api/app/routes/scripts.py`: Datei-Browser, Lesen/Speichern, Neue Datei, Test-Runner
- [x] C3 – `api/app/templates/ui/scripts.html`: Monaco Editor + Test-Runner UI
- [x] C4 – Structured Log Viewer in `order_detail.html` (JSON-Logs strukturiert anzeigen)

**Infrastruktur**
- [x] Nav in `base.html` erweitert (Asset-Typen, Runbooks, Scripts)
- [x] Alle neuen Router in `main.py` registriert
- [x] Migration im laufenden Container via `docker cp` + `alembic upgrade head` angewendet
- [x] Jinja2-Konflikt mit JS `{{`/`}}` behoben (String-Konkatenation statt Template-Literal)

### [erledigt] CLAUDE.md aufgeteilt — 2026-02-25
- [x] Allgemeine Guidelines → `~/.claude/CLAUDE.md` (global, alle Projekte)
- [x] Projektspezifische Infos bleiben in `CLAUDE.md` (aktualisiert auf neuen Stand)
