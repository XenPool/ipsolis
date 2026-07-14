# ip·Solis — Disaster-Recovery-Runbook (Backup & Restore)

**Geltungsbereich:** Wiederherstellung einer verlorenen ip·Solis-Instanz auf einem
**frischen Host** aus einem Datenbank-Backup. Das ist *Disaster Recovery*, nicht
Hochverfügbarkeit — für Hot-Failover siehe die Postgres-Standby-/Multi-Instanz-Abschnitte
in [DEPLOYMENT.de.md](DEPLOYMENT.de.md).

Für einen **In-Place**-Restore zwischen Backups auf einer noch laufenden Instanz (z. B.
zum Rückgängigmachen einer fehlerhaften Änderung) stattdessen **Admin → Wartung → Backups
→ Wiederherstellen** verwenden — dort wird automatisch ein Pre-Restore-Sicherheitsbackup
angelegt. Dieser Runbook gilt für den Fall, dass der Server selbst weg ist.

> **Status:** einmal von Hand durchgespielt (reiner Doku-Task). Bewusst kein
> automatisierter Restore-Test — siehe [AUDIT-FINDINGS.md](../AUDIT-FINDINGS.md) A4.

---

## 1. Was ein Backup enthält — und was nicht

ip·Solis-Backups sind `pg_dump` (Plain-SQL, `--no-owner --no-privileges`), durch gzip
gepipet, abgelegt in `./backups/` auf dem Host als `xp_backup_<timestamp>.sql.gz`
(Worker: [`maintenance.py`](../worker/tasks/modules/maintenance.py) `_run_backup_sync`).

**Im Dump enthalten (wird automatisch mit wiederhergestellt):**
- Alle Anwendungsdaten: `orders`, `asset_pool`, `asset_types`, `audit_log`, Runbooks, …
- **`app_config` — inklusive Fremdsystem-Zugangsdaten im Klartext.** Passwörter für AD,
  SMTP, vSphere/XenServer, SCCM usw. liegen als Klartext in `app_config.value`
  ([`config.py`](../api/app/models/config.py) — `is_secret` **maskiert nur die UI**,
  verschlüsselt **nicht**). Nach dem Restore funktionieren **E-Mail-Versand und
  AD-Zugriff also sofort** — diese Credentials müssen nicht neu eingegeben werden.
- `admin_users` (gehashte Passwörter) und `api_tokens` (gehasht) — Admins können sich
  wieder anmelden.

**NICHT im Dump (muss separat übernommen / neu erzeugt werden):**
- **`API_SECRET_KEY`** — liegt in `.env`, nicht in der Datenbank. Er signiert die
  Approval- und Zertifizierungs-Token-URLs. Nutzt der neue Server einen *anderen*
  Schlüssel, werden bereits versendete signierte Links (Approval-/Zertifizierungs-Mails
  und Teams-Karten) ungültig; neu ausgestellte funktionieren. **Denselben `API_SECRET_KEY`
  übernehmen**, damit laufende Links gültig bleiben.
- **Externalisierte Secrets** — speichert der Tenant Credentials in einem externen Vault,
  enthält der Dump nur die *Referenz* (`vault://`, `ccp://`, `azurekv://`, `awssm://`,
  `conjur://` — [`secrets.py`](../api/app/utils/secrets.py)), nicht den Wert. Siehe §5.
- **`.env`** allgemein (DB-User/Passwort, Ports, Broker-URLs).
- **TLS-Zertifikate** (`certs/`) und die **kommerzielle Lizenz** (`licenses/*.lic`).
- Die **Backup-Dateien selbst** (`backups/`) — halte eine Off-Box-Kopie des aktuellsten
  Dumps vor.

---

## 2. Voraussetzungen auf der Recovery-Seite

Diese Dinge *vor* dem Start bereithalten (idealerweise off-box / im Secret-Manager):

- [ ] Das aktuellste Backup, z. B. `xp_backup_20260714_020000.sql.gz`.
- [ ] Die alte `.env` — oder mindestens dieselben Werte für `API_SECRET_KEY`,
      `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`, `ADMIN_API_KEY`,
      `WEBHOOK_SECRET_TOKEN` und die `CELERY_*`-URLs.
- [ ] **Bei at-rest-verschlüsselten Backups** (`*.sql.gz.enc`): derselbe
      `BACKUP_ENCRYPTION_KEY` — ohne ihn lässt sich ein verschlüsselter Dump
      nicht entschlüsseln und ist unwiederbringlich. Infra-Secret in `.env`,
      nicht im Dump.
- [ ] Das Deploy-Verzeichnis / die Compose-Dateien (`docker-compose.yml` +
      `docker-compose.prod.yml`).
- [ ] TLS-Zertifikate (`certs/`) oder Akzeptanz eines frischen Self-Signed-Zertifikats
      (`tools/install/bootstrap-certs.sh`).
- [ ] Die `.lic`-Lizenzdatei (`licenses/`), für Pro-Deployments.
- [ ] Bei Nutzung eines externen Secret-Stores: Netzwerk-Erreichbarkeit vom neuen Server
      + die `secret.*`-Backend-Konfigurationswerte (§5).

---

## 3. Restore-Ablauf (frischer Host → Stack → DB-Restore)

Aus dem Deploy-Verzeichnis auf dem neuen Host ausführen. Die Befehle nehmen die Defaults
`POSTGRES_USER=xpuser` / `POSTGRES_DB=ipsolis` an; anpassen, falls deine `.env` abweicht.

### 3.1 Host vorbereiten

```bash
# Docker + Docker Compose installiert, Deploy-Verzeichnis vorhanden
cd /opt/ipsolis
cp /sicherer/ort/.env .env              # ALTE .env übernehmen (gleicher API_SECRET_KEY!)
cp /sicherer/ort/xp_backup_*.sql.gz backups/
# Zertifikate: certs/ wiederherstellen ODER frisches Self-Signed-Zertifikat erzeugen
bash tools/install/bootstrap-certs.sh   # No-op, wenn certs/ bereits vorhanden
```

### 3.2 Nur die Datenbank hochfahren, dann den Dump laden

Den Dump in eine leere Datenbank laden, **bevor** App oder Migrationen sie anfassen.

```bash
# 1. Nur Postgres starten
docker compose up -d postgres

# 2. Warten, bis sie bereit ist
until docker compose exec -T postgres pg_isready -U xpuser -d ipsolis; do sleep 2; done

# 3. Leere Ziel-DB neu anlegen (sauberer Ausgangszustand, wie beim In-App-Restore)
docker compose exec -T postgres psql -U xpuser -d postgres \
  -c 'DROP DATABASE IF EXISTS ipsolis;' \
  -c 'CREATE DATABASE ipsolis OWNER xpuser;'

# 4. Den gzip-komprimierten SQL-Dump laden
gunzip -c backups/xp_backup_20260714_020000.sql.gz | \
  docker compose exec -T postgres psql -U xpuser -d ipsolis --set ON_ERROR_STOP=1
```

### 3.3 Restlichen Stack starten und Migrationen anwenden

```bash
# 5. api, worker, beat, nginx, redis, … starten
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 6. Migrationen anwenden — der Dump kann aus einem ÄLTEREN Schema stammen als das
#    aktuelle Image. Alembic überspringt bereits angewandte Migrationen, daher ist der
#    Befehl immer gefahrlos wiederholbar.
docker compose exec -T api alembic upgrade head

# 7. (optional) api + worker neu starten, damit sie die Daten sauber übernehmen
docker compose restart api worker
```

> **Warum diese Reihenfolge:** Auf einem frischen Server darf die App nicht ihre eigenen
> leeren Tabellen anlegen, bevor der Dump geladen ist. Erst den Dump in eine leere DB
> laden, *dann* `alembic upgrade head`, um eine mögliche Schema-Lücke zwischen Dump und
> laufendem Image zu schließen.

---

## 4. Schritte nach dem Restore

- [ ] **API-Tokens** — die Tabelle `api_tokens` wird mit der DB wiederhergestellt. Unter
      **Admin → API-Tokens** prüfen; alte/ungenutzte Tokens widerrufen und frische nur für
      aktive Integrationen ausstellen (siehe [DEPLOYMENT.de.md](DEPLOYMENT.de.md) §7).
- [ ] **`API_SECRET_KEY`** — sicherstellen, dass die neue `.env` **denselben** Schlüssel
      wie der alte Server trägt. Falls geändert: Genehmigenden mitteilen, dass ältere
      Approval-/Zertifizierungs-Links nicht mehr öffnen; diese Benachrichtigungen erneut
      auslösen, damit frische signierte Links erzeugt werden.
- [ ] **TLS-Zertifikate** — echte CA-Zertifikate in `certs/` wiederhergestellt oder das
      Self-Signed-Fallback akzeptiert. Nach dem Austausch `docker compose restart nginx`.
- [ ] **Lizenz** — `.lic`-Datei in `licenses/` vorhanden (Pro-Deployments).
- [ ] **Externalisierte Secrets** — nur bei Vault-Nutzung: Erreichbarkeit prüfen (§5).

---

## 5. Externalisierter-Secret-Fall (Vault / CyberArk / Azure KV / AWS SM / Conjur)

Nur relevant, wenn Credentials als Referenzen (`vault://…`, `ccp://…` usw.) statt im
Klartext gespeichert wurden. Für das Standard-Deployment (Klartext in `app_config`)
**diesen Abschnitt überspringen** — die Credentials kamen mit dem Dump zurück.

Bei Nutzung eines externen Stores, nach dem Restore:

- [ ] Der neue Host hat **Netzwerkzugriff** auf das Secret-Backend.
- [ ] Die `secret.*`-Backend-Konfiguration löst korrekt auf. Sie liegt in `app_config` und
      wurde wiederhergestellt — aber ein dort gespeichertes Vault-**Token** kann abgelaufen
      sein, oder der neue Server hat eine andere Identität (AppRole / Kubernetes-JWT /
      AWS-Rolle).
- [ ] Aus der UI prüfen: **Admin → Einstellungen → Compliance → Secret-Backend → Test**
      (Erreichbarkeit), dann ein Per-Integration-Test (AD / SMTP), um zu bestätigen, dass
      ein echtes Secret aufgelöst wird. Alle Test-Endpunkte sind in
      [DEPLOYMENT.de.md](DEPLOYMENT.de.md) aufgeführt.

---

## 6. Verifikations-Checkliste (nach dem Restore abhaken)

- [ ] **Health**: `curl -fsk https://DEIN_HOST/health` liefert `{"status": "ok"}`.
- [ ] **Admin-Login**: ein bestehender Admin (aus der wiederhergestellten DB) kann sich
      unter `/ui/` anmelden.
- [ ] **Daten-Stichprobe**: Order-Anzahl und Asset-Pool im Dashboard entsprechen der
      Erwartung.
- [ ] **AD-Lookup**: im Bestellformular löst die Benutzervalidierung (Deputy / RDP /
      Admin-Felder) Namen auf — belegt, dass die wiederhergestellten AD-Credentials
      funktionieren.
- [ ] **E-Mail**: eine Testbestellung absenden und bestätigen, dass die Benachrichtigung
      ankommt — belegt, dass die wiederhergestellten SMTP-Credentials funktionieren.
- [ ] **Approval-Link**: den signierten Review-Link aus einer Approval-Mail / Teams-Karte
      öffnen — belegt, dass `API_SECRET_KEY` korrekt übernommen wurde (ein geänderter
      Schlüssel lässt alte Links fehlschlagen; neu ausgestellte funktionieren weiterhin).
- [ ] **Portal-Login**: der **Test** eines OIDC-Providers besteht und ein echter Login
      gelingt.

---

## 7. Rollback

- **In-App-Restore** (Admin → Wartung) legt vor dem Überschreiben immer ein
  Pre-Restore-Sicherheitsbackup an (`xp_backup_pre_restore_<timestamp>.sql.gz`) — dieses
  wiederherstellen, um rückgängig zu machen.
- **Dieser CLI-DR-Weg**: den vorherigen Dump aufbewahren. Zum Rollback §3.2 mit der
  älteren Datei wiederholen.

---

## 8. Verwandt

- [DEPLOYMENT.de.md](DEPLOYMENT.de.md) — vollständiges Produktions-Deployment,
  Backup-Planung, Secret-Backends, Per-Integration-Test-Endpunkte.
- [onboarding/INSTALL.md](onboarding/INSTALL.md) — kurze Backup/Restore-Hinweise (EN).
- Englische Version: [DR-RUNBOOK.md](DR-RUNBOOK.md).
