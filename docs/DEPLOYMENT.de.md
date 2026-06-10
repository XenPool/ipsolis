# ip·Solis -- Produktions-Deployment-Leitfaden

Diese Anleitung führt durch die Einrichtung der ip·Solis-Plattform auf einem neuen On-Premises-Server. Vorkenntnisse des Quellcodes sind nicht erforderlich.

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#1-voraussetzungen)
2. [Software beziehen](#2-software-beziehen)
3. [Umgebungsvariablen konfigurieren](#3-umgebungsvariablen-konfigurieren)
4. [SSL / TLS-Zertifikat einrichten](#4-ssl--tls-zertifikat-einrichten)
5. [Produktions-Compose-Overlay erstellen](#5-produktions-compose-overlay-erstellen)
6. [Stack starten](#6-stack-starten)
7. [Ersteinrichtung Administrator](#7-ersteinrichtung-administrator)
8. [Entra ID SSO (Portal-Authentifizierung)](#8-entra-id-sso-portal-authentifizierung)
9. [Deployment überprüfen](#9-deployment-überprüfen)
10. [Backup & Wartung](#10-backup--wartung)
11. [Update auf neue Version](#11-update-auf-neue-version)
12. [Hochverfügbarkeit](#12-hochverfügbarkeit)
13. [Fehlerbehebung](#13-fehlerbehebung)

---

## 1. Voraussetzungen

### Serveranforderungen

| Komponente | Minimum | Empfohlen |
|---|---|---|
| Betriebssystem | Linux (Debian/Ubuntu empfohlen) | Ubuntu 22.04 LTS oder neuer |
| CPU | 2 Kerne | 4 Kerne |
| RAM | 4 GB | 8 GB |
| Festplatte | 20 GB | 50 GB (abhängig von der Anzahl verwalteter Assets) |

### Software

Vor der Installation folgendes einrichten:

- **Docker Engine** >= 24.0 -- [Docker installieren](https://docs.docker.com/engine/install/)
- **Docker Compose** >= 2.20 (im Docker Engine-Paket enthalten)
- **Git** -- zum Klonen des Repositorys

Nach der Docker-Installation den Deployment-User der `docker`-Gruppe hinzufügen,
damit `docker compose`-Befehle ohne `sudo` ausgeführt werden können:

```bash
sudo usermod -aG docker $USER
# Anschließend ab- und wieder anmelden (oder: newgrp docker)
```

Installation überprüfen:

```bash
docker --version        # Docker version 24.x oder höher
docker compose version  # Docker Compose version v2.20 oder höher
git --version
```

### Netzwerkanforderungen

Der Server benötigt ausgehenden Zugriff auf:

| Ziel | Zweck |
|---|---|
| Active Directory / LDAP-Server (Port 389 oder 636) | Benutzervalidierung, Vorgesetztensuche, Gruppenmitgliedschaft |
| SMTP-Relay | E-Mail-Benachrichtigungen |
| vSphere / XenServer (falls verwendet) | VM-Lifecycle-Automatisierung |
| SCCM-Server (falls verwendet) | Tasksequenz-Trigger |

Eingehend: Die Ports **80** und **443** müssen von den Browsern der Nutzer erreichbar sein.

---

## 2. Software beziehen

Repository klonen und Images beziehen — keine Authentifizierung erforderlich:

```bash
cd /opt
sudo git clone https://github.com/XenPool/ipsolis.git ipsolis
cd ipsolis
```

Die Docker-Images (`ghcr.io/xenpool/ipsolis-api` und `ghcr.io/xenpool/ipsolis-worker`) sind öffentlich und werden beim Start des Stacks automatisch heruntergeladen.

> **Lizenzierung:** ip·Solis ist für nicht-kommerzielle Nutzung und Evaluierung kostenlos.
> Für kommerzielle Nutzung ist eine Lizenz erforderlich — siehe [LICENSE](../LICENSE) und
> Kontakt **sales@xenpool.de** für den Kauf.

---

## 3. Umgebungsvariablen konfigurieren

Beispieldatei kopieren und bearbeiten:

```bash
cp .env.example .env
nano .env
```

### Pflichtfelder

```ini
# Sichere Datenbankzugangsdaten
POSTGRES_PASSWORD=<sicheres-passwort-generieren>

# Sichere API-Secrets -- zufällige Zeichenketten mit mindestens 32 Zeichen
API_SECRET_KEY=<zufallszeichenkette-min-32-zeichen>
WEBHOOK_SECRET_TOKEN=<zufallszeichenkette>
ADMIN_API_KEY=<zufallszeichenkette-min-32-zeichen>

# CORS -- auf die Produktionsdomain setzen
CORS_ORIGINS=https://selfservice.ihreunternehmen.de
FLOWER_PASSWORD=<sicheres-passwort>
```

> **Tipp**: Sichere Passwörter generieren mit:
> ```bash
> openssl rand -base64 32
> ```

---

## 4. SSL / TLS-Zertifikat einrichten

Die Plattform läuft hinter einem nginx-Reverse-Proxy, der SSL terminiert. Ein TLS-Zertifikat und ein privater Schlüssel werden benötigt.

### Option A: Internes / selbst-signiertes Zertifikat (Intranet)

Wenn der Server nur im Unternehmensnetzwerk erreichbar ist, [mkcert](https://github.com/FiloSottile/mkcert) für ein vertrauenswürdiges Zertifikat verwenden:

```bash
# mkcert installieren (einmalig)
# Ubuntu/Debian:
sudo apt install -y libnss3-tools
sudo curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
sudo chmod +x mkcert-v*-linux-amd64
sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert

# Lokale CA in den System-Trust-Store installieren
sudo mkcert -install

# Zertifikat für den Hostnamen generieren
sudo mkdir -p certs
sudo mkcert -cert-file certs/cert.pem -key-file certs/key.pem selfservice.ihreunternehmen.de
```

> **Wichtig**: Damit Browser auf anderen Rechnern diesem Zertifikat vertrauen, muss die
> Root-CA (`mkcert -CAROOT` zeigt den Pfad) via Gruppenrichtlinie oder den
> unternehmensinternen CA-Trust-Store auf die Client-Rechner verteilt werden.

### Option B: Zertifikat der internen CA (Empfohlen für Produktion)

Wenn die Organisation eine interne Zertifizierungsstelle betreibt (z. B. Active Directory Certificate Services):

1. CSR auf dem Server erzeugen:
   ```bash
   sudo mkdir -p certs
   sudo openssl req -new -newkey rsa:2048 -nodes \
     -keyout certs/key.pem \
     -out certs/server.csr \
     -subj "/CN=selfservice.ihreunternehmen.de"
   ```
2. `certs/server.csr` bei der CA einreichen und das signierte Zertifikat erhalten.
3. Das signierte Zertifikat als `certs/cert.pem` speichern.
4. Falls die CA ein Zwischen-/Kettenzertifikat liefert, an `cert.pem` anhängen:
   ```bash
   cat signiertes-zertifikat.pem zwischen-ca.pem | sudo tee certs/cert.pem > /dev/null
   ```

### Option C: Let's Encrypt (öffentlich erreichbare Server)

Wenn der Server öffentlich zugänglich ist, können kostenlose Zertifikate von Let's Encrypt genutzt werden:

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d selfservice.ihreunternehmen.de

# Symlinks in das certs-Verzeichnis
sudo mkdir -p certs
sudo ln -sf /etc/letsencrypt/live/selfservice.ihreunternehmen.de/fullchain.pem certs/cert.pem
sudo ln -sf /etc/letsencrypt/live/selfservice.ihreunternehmen.de/privkey.pem certs/key.pem
```

#### Automatische Erneuerung einrichten (nur Option C)

```bash
# Erneuerung testen
sudo certbot renew --dry-run

# Cron-Job zum Neuladen von nginx nach der Erneuerung
echo "0 3 * * * certbot renew --quiet --post-hook 'docker exec ipsolis-nginx nginx -s reload'" | sudo crontab -
```

### nginx konfigurieren

Das Repository enthält bereits eine fertige `nginx/nginx.conf` mit dem Platzhalter `YOUR_HOSTNAME`. Die Platzhalter durch den tatsächlichen Hostnamen ersetzen (der Platzhalter kommt zweimal vor, `sed` ersetzt beide):

```bash
sudo sed -i 's/YOUR_HOSTNAME/selfservice.ihreunternehmen.de/g' nginx/nginx.conf
```

Die Datei sieht danach so aus (zur Kontrolle):

```nginx
server {
    listen 80;
    server_name selfservice.ihreunternehmen.de;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name selfservice.ihreunternehmen.de;

    ssl_certificate     /etc/nginx/certs/cert.pem;
    ssl_certificate_key /etc/nginx/certs/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 2g;

    # WebSocket / HTMX-Unterstützung
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

> Den tatsächlichen Hostnamen auch im Zertifikatsgenerierungsschritt (Option A/B/C) verwenden.

---

## 5. Produktions-Compose-Overlay

`docker-compose.prod.yml` liegt bereits im Repository und muss nicht angelegt werden.
Das Overlay fügt nginx für die SSL-Terminierung hinzu und entfernt die Dev-Bind-Mounts
aus `api` und `worker`. Kein weiterer Schritt nötig.

---

## 6. Stack starten

```bash
cd /opt/ipsolis

# Alle Dienste bauen und starten
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

# Datenbankmigrationen ausführen
docker compose exec -T api alembic upgrade head

# Prüfen ob alle Container laufen
docker compose ps
```

Erwartete Ausgabe -- alle Dienste sollten `Up (healthy)` zeigen:

```
NAME             STATUS
ipsolis-postgres      Up (healthy)
ipsolis-redis         Up (healthy)
ipsolis-api           Up (healthy)
ipsolis-worker        Up (healthy)
ipsolis-beat-1        Up
ipsolis-nginx         Up
```

Der Beat-Container hat keinen festen `container_name`, damit er für HA skaliert werden kann --
`docker compose up -d --scale beat=N` fügt Replikas hinzu.

Anwendung überprüfen:

```bash
# Direkter API-Health-Check
curl -f http://localhost:8000/health

# Über nginx (HTTPS)
curl -fsk https://selfservice.ihreunternehmen.de/health
```

---

## 7. Ersteinrichtung Administrator

### Erstes Admin-Konto (RBAC)

**https://selfservice.ihreunternehmen.de/ui/** im Browser öffnen. Beim allerersten Aufruf
(wenn `admin_users` leer ist) zeigt die Login-Seite ein **„Ersten Administrator anlegen"**-Formular
anstelle des normalen Anmeldeformulars. Folgende Felder ausfüllen:

| Feld | Hinweise |
|---|---|
| Benutzername | 3–128 Zeichen, erlaubt: `[a-zA-Z0-9._@-]+`. Wird beim Speichern kleingeschrieben. |
| Passwort | Mindestens 12 Zeichen. PBKDF2-SHA256 / 600k Iterationen (OWASP-2023). |
| Passwort bestätigen | Muss übereinstimmen. |

Das Absenden erstellt den ersten **Superadmin** und meldet ihn direkt an.
Dieser Vorgang ist idempotent gegenüber Race-Conditions -- wenn zwei Operatoren gleichzeitig
das Formular abschicken, gewinnt nur einer; der andere erhält die Meldung, das Anmeldeformular
zu verwenden.

Nach dem ersten Superadmin wechselt das Formular auf die reguläre Benutzername/Passwort-Anmeldung.

### Weitere Admin-Benutzer anlegen

Nach der Anmeldung zu **Admin-Benutzer** in der linken Navigation navigieren (nur Superadmin).
Pro-Benutzer-Konten in der jeweils passenden Rolle anlegen:

```
superadmin > admin > approver > auditor > helpdesk
```

Die vollständige Rollenhierarchie, Asset-Typ-spezifische ACL-Berechtigungen, Funktionstrennung
und Passwortrichtlinien sind in der Admin-Oberfläche unter Einstellungen → Zugangskontrolle konfigurierbar.

### Legacy-Fallback `ADMIN_API_KEY`

Der `ADMIN_API_KEY` aus `.env` authentifiziert weiterhin als **virtueller Superadmin**,
auch nach der Ersteinrichtung -- damit bestehende Skripte / `X-Admin-Key`-Header beim Update
nicht brechen. Verwendung auf der Login-Seite: **Benutzername** leer lassen, den Key als
**Passwort** eingeben. Das Audit-Log zeigt `admin:legacy_key` als Attribution, damit
Prüfer den Fallback-Pfad erkennen.

Für neue Integrationen werden **per-Integration-API-Tokens** empfohlen (Admin-Oberfläche
→ *API-Tokens*) -- benannte, ablaufende, widerrufliche Bearer-Tokens mit optionaler
Rollenbindung und Berechtigungseinschränkung. Der Legacy-Shared-Key bleibt nur für
Rückwärtskompatibilität.

### Lizenz installieren

Für Evaluierung und nicht-kommerzielle Nutzung ist keine Lizenzdatei erforderlich. Für kommerzielle
Deployments stellt XenPool nach dem Kauf eine signierte `.lic`-Datei bereit.

Installation über die Admin-Oberfläche:

1. Zu **Admin → Lizenz** navigieren (oder `https://selfservice.ihreunternehmen.de/ui/license` öffnen).
2. **Lizenz hochladen** klicken und die `ipsolis.lic`-Datei auswählen.
3. Die Seite lädt mit Lizenznehmername und Ablaufdatum neu -- kein Neustart erforderlich.

**Kulanzfrist**: Bei Ablauf einer Lizenz gilt eine 30-tägige Kulanzfrist, bevor der Status
auf „nicht lizenziert" zurückfällt. Die Admin-Oberfläche zeigt ein Warnbanner und der tägliche
Health-Alert-E-Mail wird während dieser Zeit täglich versandt.

**Überschreiben**: Eine neue `.lic` kann jederzeit hochgeladen werden, um zu verlängern.
Die alte Datei wird ersetzt; der Lizenz-Cache wird beim nächsten Request aktualisiert
(mtime-basiert, kein Downtime).

**Umgebungsvariablen-Override** (Air-Gap / automatisierte Deployments): Die `.lic`-Datei in
den Container an einem alternativen Pfad einbinden und setzen:

```bash
IPSOLIS_LICENSE_PATH=/run/secrets/ipsolis.lic
```

Der Standardpfad ist `/app/license/ipsolis.lic` (innerhalb des `ipsolis-api`-Containers).
Docker-Secrets oder ein Bind-Mount funktionieren beides.

### Konfigurationscheckliste

Zu **Admin > Einstellungen** navigieren und Folgendes konfigurieren:

#### Active Directory (Pflicht)

| Einstellung | Beschreibung | Beispiel |
|---|---|---|
| `ad.server` | AD-Domänencontroller-Hostname oder IP | `dc01.ihreunternehmen.de` |
| `ad.port` | LDAP-Port | `389` (oder `636` für LDAPS) |
| `ad.base_dn` | Such-Base-DN | `DC=ihreunternehmen,DC=de` |
| `ad.domain` | NetBIOS-Domänenname | `IHREUNTERNEHMEN` |
| `ad.username` | Dienstkonto (sAMAccountName) | `svc-selfservice` |
| `ad.password` | Dienstkonto-Passwort | *(als Secret markiert)* |
| `ad.use_ssl` | LDAPS verwenden | `true` oder `false` |

> Das Dienstkonto benötigt **Nur-Lesen**-Zugriff auf Benutzerobjekte (Attribute:
> `mail`, `displayName`, `sAMAccountName`, `userPrincipalName`, `manager`, `memberOf`).

#### SMTP (Pflicht für Benachrichtigungen)

| Einstellung | Beschreibung | Beispiel |
|---|---|---|
| `smtp.host` | SMTP-Relay-Hostname | `smtp.ihreunternehmen.de` |
| `smtp.port` | SMTP-Port | `587` |
| `smtp.user` | SMTP-Benutzername (falls Auth erforderlich) | `selfservice@ihreunternehmen.de` |
| `smtp.password` | SMTP-Passwort | *(als Secret markiert)* |
| `smtp.tls` | STARTTLS verwenden | `true` |
| `smtp.from` | Absender-E-Mail-Adresse | `noreply@ihreunternehmen.de` |
| `smtp.from_name` | Absender-Anzeigename | `ip·Solis` |

#### E-Mail-Vorlagen

Zu **Admin > E-Mail-Vorlagen** navigieren, um Benachrichtigungs-E-Mails anzupassen.
Standard-Vorlagen werden bei der Migration angelegt. Betreffzeile und Text können mit
`{{variable}}`-Platzhaltern angepasst werden.

#### Portal-Einstellungen

| Einstellung | Beschreibung | Standard |
|---|---|---|
| `portal.max_advance_days` | Wie weit im Voraus Benutzer Bestellungen planen können | `0` (unbegrenzt) |
| `portal.app_title` | Anwendungstitel im Portal | `ip·Solis` |

### Ersten Asset-Typ anlegen

1. Zu **Admin > Asset-Typen > Neu** navigieren
2. Name, Beschreibung und Kategorie ausfüllen
3. Automatisierungsstrategie konfigurieren (Gruppenzugriff, Runbook oder Zusammengesetzt)
4. Bei Bedarf Genehmigungsanforderungen setzen
5. Optional Zugriff mit einer Gruppe für berechtigte Antragsteller einschränken
6. Speichern

### Runbooks anlegen (falls zutreffend)

Wenn Asset-Typen Runbook-Automatisierung verwenden:

1. Zu **Admin > Runbooks > Neu** navigieren
2. Schritte definieren (PowerShell-Module oder eingebaute Module)
3. Das Runbook mit einem Asset-Typ verknüpfen

---

## 8. Entra ID SSO (Portal-Authentifizierung)

Das Self-Service-Portal unterstützt Microsoft Entra ID (Azure AD) für Single Sign-On.

### App in Entra ID registrieren

1. Im [Azure-Portal](https://portal.azure.com) zu **App-Registrierungen** > **Neue Registrierung**
2. Name: `ip·Solis`
3. Umleitungs-URI: `https://selfservice.ihreunternehmen.de/portal/auth/callback` (Web)
4. **Anwendungs-ID (Client)** und **Verzeichnis-ID (Mandant)** notieren
5. Unter **Zertifikate & Geheimnisse** ein neues Client-Secret erstellen

### In Admin-Oberfläche konfigurieren

Zu **Admin > Einstellungen** navigieren und einstellen:

| Einstellung | Beschreibung |
|---|---|
| `entra.mode` | `entra_only` (Entra-ID-Anmeldung erforderlich) oder `entra_with_onprem` (Entra ID + On-Premises-LDAP-Prüfung) |
| `entra.client_id` | Anwendungs-ID (Client) |
| `entra.client_secret` | Client-Secret-Wert *(als Secret markiert)* |
| `entra.tenant_id` | Verzeichnis-ID (Mandant) |
| `entra.redirect_uri` | `https://selfservice.ihreunternehmen.de/portal/auth/callback` |
| `entra.allowed_domains` | Kommagetrennte Liste erlaubter E-Mail-Domänen, z. B. `ihreunternehmen.de` |

Die Schaltfläche **Entra-Verbindung testen** zur Überprüfung der Konfiguration verwenden.

> Wenn `entra.mode` auf `disabled` gesetzt ist, ist das Portal für jeden im Netzwerk
> mit einer gemeinsamen anonymen Identität offen -- jeder Besucher sieht dieselben Bestellungen
> und kann damit interagieren. Dies nur für Demo- / Air-Gap-Lab-Deployments verwenden.
> Für Mehrbenutzer-Produktion `entra.mode = entra_only` setzen.

---

## 9. Deployment überprüfen

Diese Checkliste durcharbeiten, um die korrekte Funktion zu bestätigen:

- [ ] **HTTPS**: `https://selfservice.ihreunternehmen.de` lädt mit gültigem Zertifikat
- [ ] **Admin-Oberfläche**: `https://selfservice.ihreunternehmen.de/ui/` erreichbar
- [ ] **Ersteinrichtung**: Admin-Login zeigt „Ersten Administrator anlegen"-Formular (oder bei vorhandenem Konto das reguläre Anmeldeformular ohne Fehler)
- [ ] **Setup-Checkliste**: Dashboard zeigt die In-App-Setup-Checkliste; grundlegende Punkte nach Konfiguration abhaken
- [ ] **Portal-Anmeldung**: Benutzer können sich per Entra ID SSO anmelden
- [ ] **AD-Suche**: Im Bestellformular werden Benutzer in Stellvertreter-, RDP- und Admin-Feldern korrekt aufgelöst
- [ ] **E-Mail**: Testbestellung einreichen und Eingang der Benachrichtigungs-E-Mail bestätigen
- [ ] **Health-Check**: `curl -fsk https://selfservice.ihreunternehmen.de/health` gibt `{"status": "ok"}` zurück
- [ ] *(optional)* **API-Tokens**: Per-Integration-Token für Automatisierungen ausstellen, die bisher `X-Admin-Key` verwenden
- [ ] *(optional)* **SIEM-Streaming**: Unter *Einstellungen → Compliance* konfigurieren, falls Splunk / Sentinel / generischer Webhook-Empfänger vorhanden
- [ ] *(optional)* **Prometheus**: `/metrics` von der Monitoring-Lösung abfragen; das Dashboard liegt unter [docs/grafana/](grafana/)

---

## 10. Backup & Wartung

### Datenbank-Backup

Die PostgreSQL-Daten liegen in einem Docker-Volume (`postgres_data`). Regelmäßige Sicherungen durchführen:

```bash
# Datenbank dumpen
docker compose exec -T postgres pg_dump -U xpuser ipsolis > backup_$(date +%Y%m%d).sql

# Aus Backup wiederherstellen
cat backup_20260414.sql | docker compose exec -T postgres psql -U xpuser ipsolis
```

### Logs

Container-Logs anzeigen:

```bash
# Alle Dienste
docker compose logs --tail=50

# Einzelner Dienst
docker compose logs api --tail=100 -f    # Follow-Modus
docker compose logs worker --tail=100
```

### Festplattenbereinigung

Alte Docker-Images regelmäßig entfernen:

```bash
docker image prune -f
```

---

## 11. Update auf neue Version

```bash
cd /opt/ipsolis

# Neuesten Code holen
git pull origin main

# Neu bauen und starten
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

# Neue Datenbankmigrationen ausführen
docker compose exec -T api alembic upgrade head

# nginx neu laden, um neue Container-IPs zu übernehmen
docker compose exec -T nginx nginx -s reload

# Gesundheit prüfen
curl -fsk https://selfservice.ihreunternehmen.de/health
```

> Migrationen können mehrfach ausgeführt werden -- Alembic verfolgt, welche bereits
> angewendet wurden, und überspringt diese. Jede Feature-Version bringt in der Regel
> eine eigene Migration mit; `api/alembic/versions/` zwischen Updates auf Änderungen
> prüfen, und `docker compose exec api alembic history` zeigt die Migrationshistorie.

### Backup vor dem Update

Immer zuerst die Datenbank sichern -- `pg_dump` aus dem Postgres-Container,
oder die In-App-Funktion **Wartung → Backups** (Admin-Oberfläche) verwenden,
die einen zeitgestempelten SQL-Dump in das eingebundene `./backups/`-Verzeichnis schreibt.
Im selben Bereich einen täglichen Backup-Zeitplan konfigurieren, damit bei
einem unerwarteten Rückschritt eine frische Sicherung verfügbar ist.

### Beat-HA-Failover während des Neustarts

Bei mehreren Beat-Replikas (`--scale beat=N`) rollt `docker compose up --build -d`
die Container nacheinander und die Leader-Sperre wechselt innerhalb von ~13 s auf
die verbleibende Replika über.
Bei Einzelinstallationen gibt es eine kurze Lücke während des Neustarts, in der
periodische Tasks nicht laufen -- in der Regel nicht merklich, da Intervalle
Minuten / Stunden betragen.

---

## 12. Hochverfügbarkeit

ip·Solis ist für horizontale Skalierung auf jeder Schicht ausgelegt -- mit Ausnahme von Postgres
(Single-Writer by Design). Der Beat-Scheduler unterstützt Multi-Replika-HA über celery-redbeat;
dieser Abschnitt behandelt die übrigen drei Schichten: API-Replikas hinter einem Load Balancer,
Worker-Replikas pro Celery-Queue und einen Postgres-Read-Replica + Failover-Plan.

> **Status-Hinweis**: Die Muster in diesem Abschnitt wurden gegen Single-Host-Stacks und die
> zustandslosen Verträge der Codebasis (cookie-signierte Sessions, RedBeat-gesperrter Beat,
> queue-geroutetes Celery) verifiziert.
> Der Postgres-Standby- + Failover-Plan **muss vor dem Produktiveinsatz in einer
> Staging-Umgebung real getestet werden** -- die Docs sind korrekt, aber das operative
> Runbook (Read-Replica-Promotion, Connection-String-Wechsel, Celery-Worker-Reconnect)
> wurde am projektinternen Stack noch nicht von Anfang bis Ende erprobt. Den Postgres-Abschnitt
> als Referenzarchitektur betrachten, nicht als erprobtes Playbook.

### 12.1 Multi-Replika-API

Die API ist **zustandslos** ausgelegt -- jede Replika bearbeitet jeden Request gleich,
Sticky-Session-Affinität am Load Balancer ist nicht erforderlich.

**Was sie zustandslos macht**:

* Sessions verwenden Starletttes
  [`SessionMiddleware`](https://www.starlette.io/middleware/#sessionmiddleware)
  im Cookie-Signing-Modus (`api/app/main.py`): Die gesamte Session-Nutzlast (Admin-User-ID, Rolle, CSRF-Token)
  liegt im `xp_session`-Cookie selbst, signiert mit `API_SECRET_KEY`. Keine serverseitige Session-Tabelle.
* Tokenisierte URLs (`/approve/<token>`, `/portal/certifications/review/<token>` usw.)
  sind HMAC-signiert mit demselben `API_SECRET_KEY` und nur verifizierend. Keine Replay-Tabelle.
* Jeder Request-Zustand liegt in Postgres oder Redis -- beides replika-übergreifend geteilt.

**Was jede Replika teilen MUSS**:

| Was | Warum | Wie |
|---|---|---|
| `API_SECRET_KEY` | Signiert Session-Cookies + Approval-Tokens. Verschiedene Keys pro Replika = Clients sehen "Session ungültig" / "Approval-Link abgelaufen" in der Hälfte der Fälle. | In `.env` fixieren; via `env_file:` in Compose laden, damit jede Replika dieselbe Datei liest. |
| `DATABASE_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Gemeinsames Postgres + Redis Backplane. | Wie oben. |
| Gemeinsame Filesystem-Mounts | `licenses/`, `scripts/`, `backups/` sind bind-gemountet; Replikas, die dieselben Pfade lesen, müssen denselben Inhalt sehen. Auf einem Single-Host automatisch. Auf mehreren Hosts NFS / GlusterFS / einen gemeinsamen Volume-Treiber verwenden -- oder den Inhalt in S3-kompatiblen Objektspeicher migrieren. | Single-Host-Deployments benötigen keine zusätzliche Infrastruktur. |

**Skalierungsbefehle**:

```bash
# Single-Host: API-Replika-Anzahl via Compose erhöhen
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --scale api=3

# Jede Replika über den Load Balancer prüfen
for i in 1 2 3; do
  curl -fsk https://selfservice.ihreunternehmen.de/health \
    -H 'X-Replica-Probe: '$i
done
```

**Load-Balancer-Konfigurationshinweise**:

* **Kein Sticky Session erforderlich.** Round-Robin oder Least-Connections ist ausreichend.
* **Health-Check**: `GET /health` (unauthentifiziert). Gibt `{status: ok | degraded}` zurück,
  aggregiert Datenbank-, Redis- und Beat-Liveness. Der Endpunkt ist schnell (ein Redis-Ping +
  ein DB SELECT 1), daher ist ein LB-Checkintervall von 5–10 s sinnvoll.
* **TLS-Terminierung**: Am Load Balancer belassen (oder beim bestehenden nginx-Sidecar aus Abschnitt 5).
  Replikas bearbeiten intern Plain-HTTP; das `https_only=True`-Flag auf `SessionMiddleware`
  sichert das `Secure`-Bit des Cookies unabhängig davon, wo TLS terminiert.

**Rolling-Restart beim Update**: Der Update-Ablauf in Abschnitt 11 stoppt und startet alle
Replikas gemeinsam -- bei kleinen Flotten mit ~30 s API-Downtime akzeptabel. Für Zero-Downtime-
Rollouts den `up --build -d`-Schritt in eine Per-Replika-Schleife umwandeln:

```bash
for i in 1 2 3; do
  docker compose stop api-$i
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    up --build -d --no-deps api-$i
  # Warten bis der neue Container den Health-Check besteht
  until curl -fsk http://localhost/health > /dev/null 2>&1; do
    sleep 2
  done
done
```

Dies setzt einen LB voraus, der einen Backend-Server gleichzeitig drainieren kann; beim
Standard-Round-Robin nginx-Upstream gehen In-Flight-Requests der neustartenden Replika verloren.
Die Drain-Logik liegt in der Verantwortung des Load Balancers.

### 12.2 Multi-Replika-Worker

Celery-Worker sind zustandslose Consumer -- sie lesen aus den benannten Redis-Queues und
verarbeiten Tasks. Weitere Worker hinzuzufügen ist ein einzeiliges Scale-Up; der Worker-Code
selbst ändert sich nicht.

**Queue-Topologie** (definiert in `worker/tasks/__init__.py`):

| Queue | Tasks | Warum separate Queue |
|---|---|---|
| `provision` | Bestellworkflows (`dynamic_runner`, `standalone_runner`, `ps_module_installer`, `sccm_probe`) -- alles, was AD / SCCM / vSphere / XenServer berührt. | Provisionierungsschritte führen PowerShell aus (~5–60 s/Schritt) und halten Verbindungen zu externen Systemen. Isolation verhindert, dass ein langsamer vSphere-Aufruf schnelle Haushaltstasks blockiert. |
| `notifications` | E-Mail-Versand, Teams-Card-Zustellung, Approval-Reminder, Zertifizierungs-Reminder, Kostenbenachrichtigungen. | I/O-gebunden, latenzempfindlich (ein hängender SMTP-Server soll sich nicht hinter einem 30-s-SCCM-Probe einreihen). |
| `default` | Audit-Retention-Bereinigung, SIEM-Streaming, Lizenzprüfung, Update-Checker, Kostenbericht-Snapshot, DB-Backup, API-Token-Bereinigung. | Hintergrund-Housekeeping. Hauptsächlich cron-gesteuert, niedrige Frequenz. |
| `reclaim` | Asset-Ablauf-Prüfungen (`check_expiring_assets`). | Stündlicher Beat-Task; klein, aber isoliert, damit der stündliche Tick nicht mit Bestellworkflows um einen Worker-Slot konkurriert. |

**Dimensionsempfehlungen** (Parallelität pro Queue × Replika-Anzahl):

| Pool-Größe | Empfohlene Konfiguration | Begründung |
|---|---|---|
| Lab / Einzelteam (≤50 Benutzer) | 1 Worker-Replika, `--concurrency=4 -Q provision,notifications,default,reclaim` | Alle Queues in einem Prozess; Parallelität 4 reicht für typisch 1–2 Bestellungen/Stunde. |
| Mittel (≤500 Benutzer, ≤20 Bestellungen/Stunde) | 2 Worker-Replikas nach Queue aufgeteilt: Replika A `-Q provision --concurrency=4`, Replika B `-Q notifications,default,reclaim --concurrency=2` | Provisionierungslatenz bleibt durch Replika A begrenzt; Replika B erledigt Housekeeping + Erinnerungen ohne Head-of-Line-Blocking. |
| Groß (≥500 Benutzer, ≥50 Bestellungen/Stunde, regulierte SLAs) | 3+ Worker-Replikas: dedizierte `provision`-Worker (`--concurrency=8` × 2 Replikas), eine `notifications`-Replika (`--concurrency=4`), eine `default,reclaim`-Replika (`--concurrency=2`) | Per-Queue-Skalierung passt zur tatsächlichen Lastform. |

**Skalierungsbefehl** (Single-Host, alle Queues auf jeder Replika):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --scale worker=3
```

**Dedizierte Replikas pro Queue** erfordern entweder separate Compose-Service-Definitionen
(z. B. `worker-provision`, `worker-notifications`) mit jeweils eigenem `command:` zum
Überschreiben der Standard-Queue-Liste, oder ein Runtime-`command:`-Override:

```yaml
# docker-compose.prod.yml — Queue-Aufteilung
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

**Liveness**: Jeder Worker registriert sich beim Start via Celery-Mingle; ein frischer
Worker ist innerhalb weniger Sekunden für Beat / andere Worker sichtbar. Kein separater
Health-Check nötig -- wenn der Worker-Container `Up` ist, konsumiert er.

**Sichtbarkeit**: Flower (der bestehende `flower`-Dienst in der Dev-Compose; siehe
`docker-compose.yml`) zeigt Live-Worker-Registrierung, Queue-Tiefe und task-genaue
Dauern. Für Produktion mit derselben nginx-Auth wie die Admin-Oberfläche schützen;
Flower hat keine eingebaute Authentifizierung außer HTTP-Basic.

### 12.3 Postgres-Standby + Failover

> **Zweimal lesen**: ip·Solis arbeitet Single-Primary gegen Postgres.
> Ein Standby dient der **Disaster Recovery / Read Scale-Out**, nicht für
> aktiv-aktive Schreibzugriffe. Das Promoten eines Standbys ist ein manueller Vorgang
> (oder per Patroni / repmgr / pg_auto_failover skriptiert); der Connection-String der
> Anwendung muss auf das neue Primary umgestellt werden, und alle API-, Worker- und
> Beat-Replikas müssen neu gestartet werden, um veraltete Verbindungen aus den
> asyncpg / psycopg2-Pools zu schließen.

**Zwei ergänzende Werkzeuge**:

| Werkzeug | Funktion | Einsatzbereich |
|---|---|---|
| **Streaming Replication** (in Postgres eingebaut) | Kontinuierlicher WAL-Stream von Primary → Standby. Standby ist nur lesbar und hinkt je nach Last 10 ms bis Sekunden hinter dem Primary. | Täglicher Betrieb: Hot-Read-Replica, Near-Zero-RPO-Failover-Kandidat. |
| **pgBackRest** | Backup + PITR + Standby-Bootstrap. Speichert komprimierte verschlüsselte Backups in Objektspeicher (S3 / Azure Blob / On-Premises). | Disaster Recovery: Cold Backup, kann zu jedem Zeitpunkt innerhalb der Aufbewahrungsfrist wiederhergestellt werden, dient auch zum Bootstrap frischer Standbys ohne das Primary zu berühren. |

Produktionsdeployments verwenden typischerweise **beides**: pgBackRest für Backups +
Standby-Bootstrap, Streaming Replication für den Live-Standby.

#### 12.3.1 Streaming-Replication einrichten

Auf dem **Primary** (`ipsolis-postgres`-Container -- Konfigurations-Overlay bind-mounten,
damit es Image-Rebuilds übersteht):

```ini
# postgresql.conf Overlay (als /etc/postgresql/conf.d/replication.conf mounten)
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
synchronous_commit = on   # async-only ('off') spart einige ms pro Schreibvorgang
                          # auf Kosten unbegrenzter Verzögerung am Standby --
                          # bei 'on' belassen, sofern kein dedizierter WAL-Relay-
                          # Standby vorhanden und der Trade-off akzeptiert wird.
```

```ini
# pg_hba.conf -- Standby-Host als Replikationsbenutzer authentifizieren lassen
# (CIDR auf das Netzwerk des Standbys anpassen)
host  replication  ipsolis_repl  10.0.0.0/24  scram-sha-256
```

Replikationsbenutzer einmalig anlegen (auf dem Primary):

```sql
CREATE ROLE ipsolis_repl WITH REPLICATION LOGIN PASSWORD '<rotieren>';
SELECT pg_create_physical_replication_slot('ipsolis_standby_1');
```

Auf dem **Standby**-Host (separate VM / Container, nicht `ipsolis-postgres`):

```bash
# Standby-Datenverzeichnis vom Primary bootstrappen
pg_basebackup \
  -h <primary_host> -U ipsolis_repl -W \
  -D /var/lib/postgresql/data \
  -X stream -R --slot=ipsolis_standby_1 \
  -P
```

`-R` schreibt `standby.signal` + Verbindungsinfos in `postgresql.auto.conf`, sodass
der Standby beim nächsten Start im Hot-Standby-Modus läuft. Den Postgres-Prozess
des Standbys neu starten und überprüfen:

```sql
-- Auf dem Standby
SELECT pg_is_in_recovery();           -- → t
SELECT now() - pg_last_xact_replay_timestamp();  -- Replikationsverzögerung
```

#### 12.3.2 pgBackRest-Backup + Bootstrap

```ini
# pgbackrest.conf auf dem Primary
[global]
repo1-type=s3
repo1-s3-bucket=ipsolis-backups
repo1-s3-region=eu-central-1
repo1-s3-key=AKIA…
repo1-s3-key-secret=…
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=<rotieren>
repo1-retention-full=14
repo1-retention-diff=7

[ipsolis]
pg1-path=/var/lib/postgresql/data
```

Tägliches Vollbackup + stündliche Differentiale per Cron / systemd-Timer:

```bash
# Wöchentliches Vollbackup
pgbackrest --stanza=ipsolis backup --type=full

# Tägliches Inkrementell + WAL-Archiv
pgbackrest --stanza=ipsolis backup --type=incr
```

Wiederherstellung (PITR) für DR:

```bash
pgbackrest --stanza=ipsolis --type=time \
  --target='2026-04-30 14:30:00+02' \
  restore
```

Dies ist auch der Weg, einen frischen Standby zu bootstrappen ohne das Primary zu berühren --
`pgbackrest restore` in das Datenverzeichnis des Standbys (ersetzt den `pg_basebackup`-Schritt),
dann Postgres im Standby-Modus mit derselben `standby.signal`- + Replikationsslot-Konfiguration starten.

#### 12.3.3 Failover-Plan

Manueller Failover (ohne Patroni / repmgr -- einfach halten):

1. **Standby-Aktualität prüfen** --
   `SELECT now() - pg_last_xact_replay_timestamp()` sollte unter typischer Last < 1 s sein.
   Alles darüber bedeutet, dass In-Flight-Transaktionen verloren gehen könnten.
2. **Schreibzugriffe stoppen** -- API-, Worker- und Beat-Replikas herunterfahren,
   damit während des Failovers nichts mehr auf das Primary schreibt.
3. **Standby promoten** -- auf dem Standby-Host:

   ```bash
   pg_ctl promote -D /var/lib/postgresql/data
   ```

   Der Standby verlässt den Recovery-Modus und wird ein Read-Write-Primary.
4. **Connection-String umstellen** -- `DATABASE_URL` in `.env` auf das neue Primary ändern.
   Alle Replikas müssen aktualisiert werden; auf einem Single-Host-Stack ist das eine Dateiänderung.
5. **Alles neu starten**:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     restart api worker beat
   ```

   Die alten asyncpg / psycopg2-Pools schließen veraltete Verbindungen beim Neustart;
   neue Pools authentifizieren sich gegen das frisch promotete Primary.
6. **Totes Primary als neuen Standby aufbauen** -- sobald das ausgefallene Primary
   wiederhergestellt ist, den Standby-Bootstrap (12.3.1) gegen das *neue* Primary ausführen,
   damit die Topologie wieder eine Hot-Replica hat.

**Realistische RPO / RTO-Ziele**:

| Metrik | Nur Streaming Replication | + pgBackRest |
|---|---|---|
| Recovery Point Objective (Datenverlust) | ≤ 1 s unter normaler Last | Gleich (Streaming Replication ist der Live-Datenpfad) |
| Recovery Time Objective (Ausfall) | 5–15 Minuten (manuelle Promotion + Neustart) | Gleich -- pgBackRest beschleunigt nicht den Live-Failover, sondern die *Cold*-Recovery bei gelöschter DB |

**Automatisierung**: Das manuelle Umstellen ist für Stacks akzeptabel, bei denen 5–15 Minuten
Downtime pro Jahr tolerierbar sind. Patroni (<https://patroni.readthedocs.io/>) automatisiert
Quorum/Promotion/Connection-String-Umstellen und kann die RTO auf unter eine Minute reduzieren,
erfordert aber eine Consul / etcd / Zookeeper-Steuerungsebene neben Postgres.

#### 12.3.4 Verifikation vor dem Go-Live

Postgres-HA als **nicht getestet behandeln, bis in Staging erprobt**:

1. Standby aus einer Primary-Kopie der Staging-Daten bootstrappen.
2. `pg_is_in_recovery()` gibt `t` zurück und Replikationsverzögerung liegt unter
   1 s unter simulierter Last prüfen.
3. Primary-Container stoppen; Standby promoten; `DATABASE_URL` umstellen;
   api/worker/beat-Replikas neu starten.
4. API antwortet auf `/health` gegen das neue Primary, Testbestellung über das
   Portal aufgeben, in der `orders`-Tabelle des neuen Primarys prüfen.
5. Totes Primary als neuen Standby aufbauen und Lag-Check wiederholen.

**Bis dieser Durchlauf vollständig auf dem eigenen Stack erprobt wurde**, ist die
HA-Story „wir haben Backups + eine Read-Replica" -- nicht „wir haben verifizierten Failover".
Den Unterschied im DR-Plan dokumentieren.

---

## 13. Fehlerbehebung

### Container startet nicht

```bash
# Container-Status und Exit-Codes prüfen
docker compose ps -a

# Logs des fehlerhaften Dienstes prüfen
docker compose logs <dienstname> --tail=50
```

### Health-Check schlägt durch nginx fehl, aber API ist gesund

Nginx hat möglicherweise die alte Container-IP gecacht. Neu laden:

```bash
docker compose exec -T nginx nginx -s reload
```

### Datenbankverbindungsfehler

```bash
# Prüfen ob postgres läuft
docker compose exec postgres pg_isready -U xpuser

# Verbindung vom API-Container testen
docker compose exec api python -c "
from sqlalchemy import create_engine, text
e = create_engine('postgresql://xpuser:<passwort>@postgres:5432/ipsolis')
with e.connect() as c: print(c.execute(text('SELECT 1')).scalar())
"
```

### AD / LDAP-Verbindungsprobleme

1. Netzwerkkonnektivität aus dem Container prüfen:
   ```bash
   docker compose exec api curl -v telnet://dc01.ihreunternehmen.de:389
   ```
2. AD-Einstellungen unter Admin > Einstellungen prüfen
3. API-Logs auf LDAP-Fehler durchsuchen:
   ```bash
   docker compose logs api 2>&1 | grep -i "ldap\|ad_lookup"
   ```

### E-Mails werden nicht gesendet

1. SMTP-Einstellungen unter Admin > Einstellungen prüfen
2. Worker-Logs auf SMTP-Fehler prüfen:
   ```bash
   docker compose logs worker 2>&1 | grep -i "smtp\|mail\|notification"
   ```
3. Erreichbarkeit des SMTP-Relays prüfen:
   ```bash
   docker compose exec api curl -v telnet://smtp.ihreunternehmen.de:587
   ```

### Zugriff verweigert auf certs-Verzeichnis

```bash
sudo chmod 644 certs/cert.pem
sudo chmod 600 certs/key.pem
```
