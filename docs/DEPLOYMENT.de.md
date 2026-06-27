# ip·Solis -- Produktions-Deployment-Leitfaden

Dieser Leitfaden führt Sie durch die Einrichtung der ip·Solis-Plattform auf einem frischen On-Premises-Server. Vorkenntnisse über die Codebasis sind nicht erforderlich.

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#1-voraussetzungen)
2. [Software beziehen](#2-software-beziehen)
3. [Umgebungsvariablen konfigurieren](#3-umgebungsvariablen-konfigurieren)
4. [SSL-/TLS-Zertifikat einrichten](#4-ssl--tls-zertifikat-einrichten)
5. [Produktions-Compose-Overlay erstellen](#5-produktions-compose-overlay-erstellen)
6. [Stack starten](#6-stack-starten)
7. [Initiale Admin-Einrichtung](#7-initiale-admin-einrichtung)
   - [Lizenz installieren (Pro)](#lizenz-installieren-pro)
8. [Portal-SSO — OpenID Connect (Portal-Authentifizierung)](#8-portal-sso--openid-connect-portal-authentifizierung)
9. [Deployment verifizieren](#9-deployment-verifizieren)
10. [Backup & Wartung](#10-backup--wartung)
11. [Aktualisierung auf eine neue Version](#11-aktualisierung-auf-eine-neue-version)
12. [Hochverfügbare Deployments (optional)](#12-hochverfügbare-deployments-optional)
13. [Fehlerbehebung](#13-fehlerbehebung)
14. [Sauberes Zurücksetzen (Testumgebungen)](#14-sauberes-zurücksetzen-testumgebungen)

---

---

## 1. Voraussetzungen

### Server-Anforderungen

| Komponente | Minimum | Empfohlen |
|-----------|---------|-------------|
| Betriebssystem | Linux (Debian/Ubuntu empfohlen) | Ubuntu 22.04 LTS oder neuer |
| CPU | 2 Kerne | 4 Kerne |
| RAM | 4 GB | 8 GB |
| Festplatte | 20 GB | 50 GB (abhängig von der Anzahl der verwalteten Assets) |

### Software

Installieren Sie Folgendes, bevor Sie fortfahren:

- **Docker Engine** >= 24.0 -- [Docker installieren](https://docs.docker.com/engine/install/)
- **Docker Compose** >= 2.20 (in Docker Engine enthalten)
- **Git** -- zum Klonen des Repositorys

Fügen Sie nach der Docker-Installation den Deployment-Benutzer zur `docker`-Gruppe hinzu, damit
`docker compose`-Befehle ohne `sudo` funktionieren:

```bash
sudo usermod -aG docker $USER
# Then log out and back in (or: newgrp docker)
```

Überprüfen Sie Ihre Installation:

```bash
docker --version        # Docker version 24.x or higher
docker compose version  # Docker Compose version v2.20 or higher
git --version
```

### Netzwerk-Anforderungen

Der Server benötigt ausgehenden Zugriff auf:

| Ziel | Zweck |
|-------------|---------|
| Ihr Active Directory / LDAP-Server (Port 389 oder 636) | Benutzervalidierung, Manager-Lookup, Gruppenmitgliedschaft |
| Ihr SMTP-Relay | E-Mail-Benachrichtigungen |
| vSphere / XenServer (falls zutreffend) | VM-Lifecycle-Automatisierung |
| SCCM-Server (falls zutreffend) | Auslösen von Task-Sequenzen |

Eingehend: Die Ports **80** und **443** müssen von den Browsern Ihrer Benutzer erreichbar sein.

---

## 2. Software beziehen

> **Frische Umgebung empfohlen:** Docker-Volumes (Datenbankdaten) überstehen
> `rm -rf /opt/ipsolis` — sie liegen unter `/var/lib/docker/volumes/` und bleiben
> bestehen, bis sie explizit entfernt werden. Stellen Sie für eine saubere Erstinstallation
> sicher, dass keine alten Volumes existieren.
> Siehe [Sauberes Zurücksetzen (Testumgebungen)](#14-sauberes-zurücksetzen-testumgebungen).

Repository klonen — keine Authentifizierung erforderlich:

```bash
cd /opt
sudo git clone https://github.com/XenPool/ipsolis.git ipsolis
sudo chown -R $USER:$USER ipsolis    # Repo dem User zuweisen, damit git pull / docker compose ohne sudo laufen
cd ipsolis
```

ip·Solis wird als fertiges Docker-Image bereitgestellt — Sie müssen nichts selbst bauen. Die
Images sind öffentlich (kein `docker login` nötig). Die Pull- und Start-Befehle folgen in
[Abschnitt 6](#6-stack-starten).

> **Lizenzierung:** ip·Solis ist für die private Nutzung und die 30-tägige Evaluierung
> kostenlos. Jede produktive oder organisatorische Nutzung — einschließlich öffentlicher
> Hand und gemeinnütziger Organisationen — erfordert eine kommerzielle Lizenz. Siehe
> [LICENSE](../LICENSE) und kontaktieren Sie **sales@xenpool.de** zum Erwerb.

---

## 3. Umgebungsvariablen konfigurieren

Kopieren Sie die Beispieldatei und bearbeiten Sie sie:

```bash
cp .env.example .env
nano .env
```

### Zwingend zu ändernde Einstellungen

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

> **Tipp**: Sichere Passwörter generieren Sie mit:
> ```bash
> openssl rand -base64 32
> ```

> **`.env`-Datei absichern**: Sie speichert Secrets im Klartext — beschränken Sie daher den Zugriff:
> ```bash
> chmod 600 .env
> ```
> Behandeln Sie `ADMIN_API_KEY` als Bootstrap-Zugang. Legen Sie nach der Ersteinrichtung
> RBAC-Admin-Konten an und stellen Sie Per-Integration-API-Tokens aus (Admin → API-Tokens) —
> diese werden gehasht gespeichert, sind scoped, ablaufend und widerrufbar und sind der
> bevorzugte Weg, Automatisierungen zu authentifizieren.

## 4. SSL-/TLS-Zertifikat einrichten

Die Plattform läuft hinter einem nginx-Reverse-Proxy, der SSL terminiert. Sie benötigen ein TLS-Zertifikat und einen privaten Schlüssel.

> **Wählen Sie eine** der drei Optionen unten (A, B **oder** C) — sie sind sich gegenseitig ausschließende Alternativen, je nachdem, wie Ihr Server erreichbar ist. Sobald `cert.pem` + `key.pem` in `nginx/ssl/` liegen, geht es bei [**nginx konfigurieren**](#nginx-konfigurieren) weiter — dieser Schritt ist für alle Optionen erforderlich.

### Option A: Internes / selbstsigniertes Zertifikat (Intranet)

Wenn Ihr Server nur innerhalb Ihres Unternehmensnetzwerks erreichbar ist, verwenden Sie [mkcert](https://github.com/FiloSottile/mkcert), um ein vertrauenswürdiges Zertifikat zu generieren:

```bash
# Install mkcert (one-time)
# Ubuntu/Debian:
sudo apt install -y libnss3-tools
sudo curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
sudo chmod +x mkcert-v*-linux-amd64
sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert

# Install the local CA into your system trust store
sudo mkcert -install

# Generate the certificate for your hostname  ← replace YOUR_HOSTNAME.YOUR_COMPANY.COM
sudo mkdir -p nginx/ssl
sudo mkcert -cert-file nginx/ssl/cert.pem -key-file nginx/ssl/key.pem YOUR_HOSTNAME.YOUR_COMPANY.COM
```

> **Wichtig**: Damit Browser auf anderen Rechnern diesem Zertifikat vertrauen, müssen Sie
> die Root-CA (`mkcert -CAROOT` zeigt den Pfad) über
> Gruppenrichtlinien oder Ihren Enterprise-CA-Trust-Store an die Client-Rechner verteilen.

**Root-CA auf einem Windows-Client installieren:**

```bash
# On the server — make the root CA available for download
sudo cp $(sudo mkcert -CAROOT)/rootCA.pem /tmp/ipsolis-rootCA.pem
sudo chmod 644 /tmp/ipsolis-rootCA.pem
```

Kopieren Sie die Datei auf Ihren Windows-Laptop (SCP, USB usw.), dann:

**Option 1 — per Doppelklick:**
1. Datei umbenennen in `ipsolis-rootCA.crt`
2. Doppelklick → **Zertifikat installieren**
3. **Lokaler Computer** → **Vertrauenswürdige Stammzertifizierungsstellen**
4. Browser neu starten

**Option 2 — per PowerShell (als Administrator):**
```powershell
certutil -addstore -f "ROOT" ipsolis-rootCA.crt
```

Nach der Installation vertrauen Chrome, Edge und Firefox (unter Verwendung des Windows-Trust-Stores) dem Zertifikat ohne Warnungen.

### Option B: Zertifikat von Ihrer Enterprise-CA (für Produktion empfohlen)

Wenn Ihre Organisation eine interne Zertifizierungsstelle betreibt (z. B. Active Directory Certificate Services):

1. Erzeugen Sie einen CSR auf dem Server: *(ersetzen Sie YOUR_HOSTNAME.YOUR_COMPANY.COM)*
   ```bash
   sudo mkdir -p nginx/ssl
   sudo openssl req -new -newkey rsa:2048 -nodes \
     -keyout nginx/ssl/key.pem \
     -out nginx/ssl/server.csr \
     -subj "/CN=YOUR_HOSTNAME.YOUR_COMPANY.COM"
   ```
2. Reichen Sie `nginx/ssl/server.csr` bei Ihrer CA ein und beziehen Sie das signierte Zertifikat.
3. Speichern Sie das signierte Zertifikat als `nginx/ssl/cert.pem`.
4. Falls Ihre CA ein Zwischen-/Chain-Zertifikat bereitstellt, hängen Sie es an `cert.pem` an:
   ```bash
   cat signed-cert.pem intermediate-ca.pem | sudo tee nginx/ssl/cert.pem > /dev/null
   ```

### Option C: Let's Encrypt (öffentlich erreichbare Server)

Wenn Ihr Server öffentlich erreichbar ist, können Sie kostenlose Zertifikate von Let's Encrypt verwenden:

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d YOUR_HOSTNAME.YOUR_COMPANY.COM  # ← replace

# Symlink into the ssl directory
sudo mkdir -p nginx/ssl
sudo ln -sf /etc/letsencrypt/live/YOUR_HOSTNAME.YOUR_COMPANY.COM/fullchain.pem nginx/ssl/cert.pem
sudo ln -sf /etc/letsencrypt/live/YOUR_HOSTNAME.YOUR_COMPANY.COM/privkey.pem nginx/ssl/key.pem
```

#### Automatische Erneuerung einrichten (nur Option C)

```bash
# Test renewal
sudo certbot renew --dry-run

# Add a cron job to reload nginx after renewal
echo "0 3 * * * certbot renew --quiet --post-hook 'docker exec ipsolis-nginx nginx -s reload'" | sudo crontab -
```

### nginx konfigurieren

> **Ende der Zertifikat-Optionen.** Unabhängig davon, welche Option (A, B oder C)
> Sie oben gewählt haben, geht es hier weiter — die folgenden Schritte gelten für
> **alle** Setups.

Das Repository liefert bereits eine einsatzbereite `nginx/nginx.conf` mit dem Platzhalter `YOUR_HOSTNAME.YOUR_COMPANY.COM`. Ersetzen Sie ihn durch Ihren tatsächlichen FQDN — denselben Hostnamen, den Sie oben für das Zertifikat verwendet haben (`sed` behandelt beide Vorkommen in einem Durchgang):

```bash
# ← replace ipsolis.example.com with your actual FQDN
sudo sed -i 's/YOUR_HOSTNAME.YOUR_COMPANY.COM/ipsolis.example.com/g' nginx/nginx.conf
```

Die Datei sieht danach so aus (zur Referenz):

```nginx
server {
    listen 80;
    server_name YOUR_HOSTNAME.YOUR_COMPANY.COM;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name YOUR_HOSTNAME.YOUR_COMPANY.COM;

    ssl_certificate     /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
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

> Verwenden Sie denselben Hostnamen im Schritt zur Zertifikatserzeugung (Option A/B/C oben).

---

## 5. Produktions-Compose-Overlay erstellen

`docker-compose.prod.yml` ist bereits im Repository enthalten — keine Aktion erforderlich.
Das Overlay fügt nginx für die SSL-Terminierung hinzu und entfernt die Dev-Bind-Mounts von
`api` und `worker`.

---

## 6. Stack starten

Laden Sie die fertigen Images und starten Sie den Stack. Wenn Sie `COMPOSE_FILE`
einmal setzen, verwendet jeder spätere `docker compose`-Befehl (exec, ps, logs, down)
automatisch die richtigen Dateien — Sie müssen `-f` nicht wiederholen.

Wählen Sie, welche Version laufen soll:

- **Produktion:** Setzen Sie `IPSOLIS_VERSION` auf ein bestimmtes Release, damit
  sich das laufende System nicht unerwartet ändert. Erhöhen Sie den Wert, wenn Sie
  aktualisieren möchten (siehe [Abschnitt 11](#11-aktualisierung-auf-eine-neue-version)).
- **Test / erster Versuch:** lassen Sie es leer, um immer den neuesten Build zu erhalten.

```bash
cd /opt/ipsolis

# Production — pin a tested release:
export IPSOLIS_VERSION=x.x.x   # e.g. 0.6.12

# Pre-live / test — track latest (leave IPSOLIS_VERSION unset):
# (nothing to export)

export COMPOSE_FILE=docker-compose.ghcr.yml:docker-compose.prod.yml
docker compose pull
docker compose up -d
```

**Dann — Migrationen ausführen und verifizieren:**

```bash
docker compose exec -T api alembic upgrade head   # uses $COMPOSE_FILE set above
docker compose ps
```

Erwartete Ausgabe -- alle Dienste sollten `Up (healthy)` anzeigen:

```
NAME             STATUS
ipsolis-postgres      Up (healthy)
ipsolis-redis         Up (healthy)
ipsolis-api           Up (healthy)
ipsolis-worker        Up (healthy)
ipsolis-beat-1   Up
ipsolis-nginx         Up
```

Anwendung verifizieren:

```bash
# Direct API health check
curl -f http://localhost:8000/health | python3 -m json.tool

# Through nginx (HTTPS)
curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health | python3 -m json.tool
```

---

## 7. Initiale Admin-Einrichtung

### Admin-Konto beim Erststart (RBAC)

Öffnen Sie **https://YOUR_HOSTNAME.YOUR_COMPANY.COM/ui/** in Ihrem Browser. Beim
allerersten Besuch (wenn `admin_users` leer ist) zeigt die Anmeldeseite
ein Formular **"Ersten Administrator erstellen"** statt des
normalen Anmeldeformulars. Füllen Sie aus:

| Feld | Hinweise |
|---|---|
| Benutzername | 3–128 Zeichen, erlaubt: `[a-zA-Z0-9._@-]+`. Wird beim Schreiben kleingeschrieben. |
| Passwort | ≥ 12 Zeichen. PBKDF2-SHA256 / 600k Iterationen (OWASP-2023). |
| Passwort bestätigen | Muss übereinstimmen. |

Das Absenden erstellt den ersten **superadmin** und meldet Sie automatisch an.
Dies ist idempotent gegenüber Race-Conditions — wenn zwei Operatoren das Formular
gleichzeitig absenden, gewinnt nur einer; der andere erhält die Meldung, das
"Anmeldeformular zu verwenden".

Sobald der erste superadmin existiert, wechselt das Formular zur regulären
Anmeldung mit Benutzername + Passwort.

### Weitere Admin-Benutzer hinzufügen

Navigieren Sie nach der Anmeldung in der linken Navigation zu **Admin-Benutzer**
(nur superadmin). Erstellen Sie benutzerspezifische Konten in der für jeden
Operator passenden Rolle:

```
superadmin > admin > approver > auditor > helpdesk
```

Die vollständige Rollenhierarchie, ACL-Vergaben pro Asset-Typ, die Durchsetzung der
Funktionstrennung und die Optionen für die Passwortrichtlinie sind in der Admin-UI
unter Einstellungen → Zugriffssteuerung konfigurierbar.

### Legacy-`ADMIN_API_KEY`-Fallback

Der `ADMIN_API_KEY` aus `.env` authentifiziert auch nach der Ersteinrichtung
weiterhin als **virtueller superadmin**, sodass bestehende
Skripte / `X-Admin-Key`-Header beim Upgrade nicht brechen. Um ihn
auf der Anmeldeseite zu verwenden: lassen Sie **Benutzername** leer und fügen Sie den Schlüssel in
**Passwort** ein. Die Audit-Zuordnung erscheint als `admin:legacy_key`, sodass
Auditoren erkennen können, wann der Fallback-Pfad verwendet wurde.

Für neue Integrationen sind **Integrationsspezifische API-Tokens** (Admin-UI
→ *API-Tokens*) vorzuziehen — benannte, ablaufende, widerrufbare Bearer-Tokens mit
optionaler Rollenbindung und eingeschränkten Berechtigungen. Der alte einzelne
gemeinsame Schlüssel wird nur aus Gründen der Abwärtskompatibilität beibehalten.

### Lizenz installieren

Für die private Nutzung und die 30-tägige Evaluierung ist keine Lizenzdatei erforderlich. Jedes
produktive oder organisatorische Deployment — einschließlich öffentlicher Hand und gemeinnütziger
Nutzung — erfordert eine kommerzielle Lizenz; XenPool liefert nach dem Kauf eine signierte `.lic`-Datei.

Installieren Sie sie über die Admin-UI:

1. Navigieren Sie zu **Admin → Lizenz** (oder öffnen Sie
   `https://YOUR_HOSTNAME.YOUR_COMPANY.COM/ui/license`).
2. Klicken Sie auf **Lizenz hochladen** und wählen Sie Ihre `ipsolis.lic`-Datei aus.
3. Die Seite lädt neu und zeigt Lizenznehmer-Name und Ablaufdatum an — kein Neustart erforderlich.

**Kulanzfrist**: Wenn eine Lizenz abläuft, gilt eine 30-tägige Kulanzfrist,
bevor der Lizenzstatus auf unlizenziert zurückfällt. Die Admin-UI zeigt einen
bernsteinfarbenen Warnbanner an, und die tägliche Health-Alert-E-Mail wird während
des gesamten Zeitfensters jeden Tag ausgelöst.

**Überschreiben**: Laden Sie jederzeit eine neue `.lic` hoch, um zu erneuern. Die alte Datei
wird an Ort und Stelle ersetzt; der Lizenz-Cache aktualisiert sich bei der nächsten Anfrage
(mtime-basiert, ohne Ausfallzeit).

**Umgebungsvariablen-Override** (Air-Gapped / automatisierte Deployments): Mounten Sie die
`.lic`-Datei in den Container an einem alternativen Pfad und setzen Sie:

```bash
IPSOLIS_LICENSE_PATH=/run/secrets/ipsolis.lic
```

Der Standardpfad ist `/app/license/ipsolis.lic` (innerhalb des `ipsolis-api`-
Containers). Sowohl Docker-Secrets als auch ein Bind-Mount funktionieren.

### Konfigurations-Checkliste

Die anwendungsinterne **Setup-Checkliste** im Dashboard führt Sie durch alle erforderlichen Schritte.
Die folgende Reihenfolge entspricht der Checkliste:

#### 1. Anwendungstitel und Logo festlegen *(Essenziell)*

Navigieren Sie zu **Admin > Einstellungen → Allgemein**:

| Einstellung | Beschreibung |
|---------|-------------|
| `app.title` | Im Portal und in E-Mails angezeigter Anwendungsname (Standard: `ip·Solis`) |
| `app.logo` | Logo-Upload (PNG/SVG empfohlen) |

#### 2. SMTP konfigurieren *(Essenziell)*

Navigieren Sie zu **Admin > Einstellungen → E-Mail**:

| Einstellung | Beschreibung | Beispiel |
|---------|-------------|---------|
| `smtp.host` | Hostname des SMTP-Relays | `smtp.yourcompany.com` |
| `smtp.port` | SMTP-Port | `587` |
| `smtp.user` | SMTP-Benutzername (falls Auth erforderlich) | `selfservice@yourcompany.com` |
| `smtp.password` | SMTP-Passwort | *(als geheim markiert)* |
| `smtp.tls` | STARTTLS verwenden | `true` |
| `smtp.from` | Absender-E-Mail-Adresse | `noreply@yourcompany.com` |
| `smtp.from_name` | Anzeigename des Absenders | `ip·Solis` |

Navigieren Sie zu **Admin > E-Mail-Vorlagen**, um den Text der Benachrichtigungs-E-Mails anzupassen.

#### 3. Mit Active Directory verbinden *(Essenziell)*

Navigieren Sie zu **Admin > Einstellungen → Active Directory**:

| Einstellung | Beschreibung | Beispiel |
|---------|-------------|---------|
| `ad.server` | Hostname oder IP des AD-Domänencontrollers | `dc01.yourcompany.com` |
| `ad.port` | LDAP-Port | `389` (oder `636` für LDAPS) |
| `ad.base_dn` | Such-Basis-DN | `DC=yourcompany,DC=com` |
| `ad.domain` | NetBIOS-Domänenname | `YOURCOMPANY` |
| `ad.username` | Dienstkonto (sAMAccountName) | `svc-selfservice` |
| `ad.password` | Passwort des Dienstkontos | *(als geheim markiert)* |
| `ad.use_ssl` | LDAPS verwenden | `true` oder `false` |

> Die erforderlichen AD-Berechtigungen hängen von den verwendeten Modulen und Runbook-Schritten ab.
> Als Basis:
> - **Lesen** auf Benutzerobjekten (Attribute: `mail`, `displayName`, `sAMAccountName`,
>   `userPrincipalName`, `manager`, `memberOf`, `distinguishedName`)
> - **Schreiben `member`** auf Gruppenobjekten — erforderlich für die AD-gruppenbasierte Zugriffsvergabe
>
> Je nach den eingesetzten Runbooks und Modulen können zusätzliche Berechtigungen (z. B. auf
> Computerobjekten, OUs oder anderen Attributen) erforderlich sein.

#### 4. Portal-SSO (OIDC) aktivieren *(Essenziell)*

Siehe [Abschnitt 8](#8-portal-sso--openid-connect-portal-authentifizierung) für die vollständige Einrichtung
(Entra ID, Okta und jeder andere OIDC-Anbieter).

#### 5. Ersten Asset-Typ erstellen *(Essenziell)*

1. Gehen Sie zu **Admin > Asset-Typen > Neu**
2. Tragen Sie Name, Beschreibung und Kategorie ein
3. Konfigurieren Sie die Automatisierungsstrategie (Gruppenzugriff, Runbook oder Composite)
4. Legen Sie bei Bedarf Genehmigungsanforderungen fest
5. Beschränken Sie den Zugriff optional mit einer Gruppen-DN für berechtigte Antragsteller
6. Speichern

#### 6. Mindestens ein Asset zum Pool hinzufügen *(Essenziell)*

Gehen Sie zu **Admin > Asset-Pool > Neu** und fügen Sie mindestens ein Asset hinzu.

> Für reine `capacity_pooled`-Asset-Typen (Kontingent ohne dedizierte Instanzen) kann dieser
> Schritt übersprungen werden.

#### Runbooks einrichten *(falls zutreffend)*

ip·Solis wird mit einem vollständig konfigurierten Beispiel-Runbook ausgeliefert:
**"Virtual Machine Recycler"** — ein eigenständiges Runbook, das alle erforderlichen
Skript-Module (XenServer/XCP-ng, SCCM, Active Directory) enthält und als
Vorlage für Ihre eigene Automatisierung dienen kann.

Sie finden es unter **Admin > Runbooks**, um es zu inspizieren, zu kopieren oder anzupassen.

So erstellen Sie Asset-Typ-Runbooks:

1. Gehen Sie zu **Admin > Runbooks > Neu**
2. Definieren Sie die Schritte (PowerShell-Module oder integrierte Module)
3. Verknüpfen Sie das Runbook mit einem Asset-Typ

Es kann eine beliebige Anzahl benutzerdefinierter Runbooks mit jeder Kombination von Schritten erstellt werden.

#### Empfohlene nächste Schritte

- **Microsoft-Teams-Genehmigungskarten**: Gehen Sie zu **Admin > Einstellungen → E-Mail** und fügen Sie eine
  Teams-Webhook-URL hinzu — Genehmiger erhalten zusätzlich zur E-Mail eine Adaptive Card mit einem
  Ein-Klick-Prüflink.
- **Audit-Log an SIEM streamen**: Konfigurieren Sie einen Splunk-HEC- oder Webhook-Endpunkt unter
  **Admin > Einstellungen → Compliance**.
- **Integrationsspezifische API-Tokens ausstellen**: Gehen Sie zu **Admin > API-Tokens**, um benannte,
  widerrufbare Bearer-Tokens für ServiceNow, Skripte oder Prometheus zu erstellen — ersetzt den
  gemeinsamen `X-Admin-Key`.

> **Nach einer DB-Wiederherstellung:** Die Tabelle `api_tokens` wird zusammen mit der Datenbank wiederhergestellt.
> Überprüfen Sie alle Tokens unter **Admin > API-Tokens** — widerrufen Sie alle alten oder ungenutzten Tokens
> und stellen Sie neue, dedizierte Tokens nur für aktive Integrationen aus.

---

## 8. Portal-SSO — OpenID Connect (Portal-Authentifizierung)

Das Self-Service-Portal authentifiziert Endbenutzer über **generisches OpenID Connect (OIDC)**.
Jeder standardkonforme Identity-Provider funktioniert über einen einzigen Codepfad — Entra ID,
Okta, Ping, Google Workspace, Keycloak, Authentik, Zitadel, … — da sich jeder Anbieter
über seine **Issuer-URL** anhand des Discovery-Dokuments selbst konfiguriert
(`<issuer>/.well-known/openid-configuration`). Das Hinzufügen eines neuen IdP ist ein Konfigurationseintrag, keine
Codeänderung. Eine On-Prem-AD/LDAP-Anmeldung mit Benutzername + Passwort kann neben OIDC angeboten werden.

### In der Admin-UI konfigurieren

Navigieren Sie zu **Admin → Einstellungen → Authentifizierung**:

1. **Anmeldung zum Zugriff auf das Portal erforderlich** — für den Mehrbenutzerbetrieb in der Produktion aktivieren.
   (Aus = Portal mit gemeinsamer anonymer Identität offen; nur für Demo- / Air-Gapped-Labs.)
2. *(optional)* **Zusätzlich On-Prem-AD-/LDAP-Anmeldung anbieten** — verwendet das auf derselben Seite
   konfigurierte LDAP-Dienstkonto.
3. Klicken Sie unter **OIDC-Anbieter** auf **+ Anbieter hinzufügen** und füllen Sie aus:

| Feld | Beschreibung |
|-------|-------------|
| Anbieter-ID | Stabiler URL-sicherer Slug (`a–z 0–9 _ -`), z. B. `entra`, `okta`. Erscheint in der Callback-URL und **kann später nicht geändert werden**. |
| Anzeigename | Button-Beschriftung auf der Anmeldeseite, z. B. `Entra ID`. |
| Issuer-URL | Der OIDC-Issuer (Discovery wird daraus abgeleitet). Siehe Rezepte unten. |
| Client-ID | Anwendungs-/Client-ID aus der App-Registrierung des IdP. |
| Client-Secret | Confidential-Client-Secret *(verschlüsselt gespeichert; unterstützt `vault://…` / `ccp://…`-Referenzen)*. |
| Redirect-URI | Leer lassen, um `https://YOUR_HOST/portal/auth/<provider-id>/callback` automatisch abzuleiten, oder einen expliziten Wert setzen. Registrieren Sie **genau diese URI** in der IdP-App. |
| Erlaubte Domains | *(optional)* kommagetrennte Allow-Liste von UPN-/E-Mail-Domains. Leer = beliebige erlauben. |
| *Erweitert* | Scopes (Standard `openid profile email`) und Claim-Mapping (username/email/name). |

Klicken Sie auf **Test**, um eine Discovery-Probe auszuführen (bestätigt, dass der Issuer erreichbar ist und die
Authorization-/Token-/JWKS-Endpunkte aufgelöst werden), dann auf **Speichern**.

> Wenn **mehr als eine** Anmeldemethode aktiviert ist, zeigt das Portal unter
> `/portal/login` eine Auswahl; bei genau einer leitet es direkt dorthin weiter.

#### Rezept — Microsoft Entra ID (Azure AD)

1. [Azure Portal](https://portal.azure.com) → **App-Registrierungen** → **Neue Registrierung**.
2. Redirect-URI (Web): `https://YOUR_HOST/portal/auth/entra/callback`.
3. Kopieren Sie die **Anwendungs-(Client-)ID** und die **Verzeichnis-(Tenant-)ID**; erstellen Sie ein Client-
   Secret unter **Zertifikate & Geheimnisse**.
4. In ip·Solis: Anbieter-ID `entra`, Issuer-URL
   `https://login.microsoftonline.com/<tenant-id>/v2.0`, plus Client-ID/-Secret.

#### Rezept — Okta

1. Okta Admin → **Applications** → **Create App Integration** → **OIDC / Web Application**.
2. Sign-in-Redirect-URI: `https://YOUR_HOST/portal/auth/okta/callback`.
3. Kopieren Sie die **Client-ID** und das **Client-Secret**.
4. In ip·Solis: Anbieter-ID `okta`, Issuer-URL `https://<your-org>.okta.com`
   (oder den Issuer Ihres benutzerdefinierten Authorization-Servers), plus Client-ID/-Secret.

---

## 9. Deployment verifizieren

Arbeiten Sie diese Checkliste ab, um zu bestätigen, dass alles funktioniert:

- [ ] **HTTPS**: `https://YOUR_HOSTNAME.YOUR_COMPANY.COM` lädt mit einem gültigen Zertifikat
- [ ] **Admin-UI**: `https://YOUR_HOSTNAME.YOUR_COMPANY.COM/ui/` ist erreichbar
- [ ] **Ersteinrichtung**: Der Aufruf der Admin-Anmeldung zeigt das Formular "Ersten Administrator erstellen" (oder, falls bereits erledigt, das reguläre Anmeldeformular ohne Fehler)
- [ ] **Setup-Checkliste**: Das Dashboard zeigt die anwendungsinterne Setup-Checkliste; haken Sie essenzielle Punkte ab, während Sie sie konfigurieren
- [ ] **Portal-Anmeldung**: Benutzer können sich über einen OIDC-Anbieter (Entra ID, Okta, …) anmelden — der Discovery-**Test** besteht und eine echte Anmeldung wird abgeschlossen
- [ ] **AD-Lookup**: Im Bestellformular werden bei der Benutzervalidierung (Vertreter-, RDP-, Admin-Felder) Namen aufgelöst
- [ ] **E-Mail**: Eine Testbestellung absenden und bestätigen, dass die Benachrichtigungs-E-Mail ankommt
- [ ] **Health-Check**: `curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health` gibt `{"status": "ok"}` zurück
- [ ] *(optional)* **API-Tokens**: Stellen Sie ein integrationsspezifisches Token für jede Automatisierung aus, die zuvor `X-Admin-Key` verwendet hat
- [ ] *(optional)* **SIEM-Streaming**: Unter *Einstellungen → Compliance* konfigurieren, falls Sie Splunk / Sentinel / einen generischen Webhook-Empfänger haben
- [ ] *(optional)* **Prometheus**: `/metrics` aus Ihrem Monitoring scrapen; das Dashboard wird in [docs/grafana/](grafana/) ausgeliefert

---

## 10. Backup & Wartung

### Datenbank-Backup

Die PostgreSQL-Daten werden in einem Docker-Volume (`postgres_data`) gespeichert. Sichern Sie es regelmäßig:

```bash
# Dump the database
docker compose exec -T postgres pg_dump -U xpuser ipsolis > backup_$(date +%Y%m%d).sql

# Restore from backup
cat backup_20260414.sql | docker compose exec -T postgres psql -U xpuser ipsolis
```

### Logs

Container-Logs anzeigen:

```bash
# All services
docker compose logs --tail=50

# Specific service
docker compose logs api --tail=100 -f    # follow mode
docker compose logs worker --tail=100
```

### Festplattenbereinigung

Entfernen Sie regelmäßig alte Docker-Images:

```bash
docker image prune -f
```

---

## 11. Aktualisierung auf eine neue Version

### Vor dem Upgrade sichern

Erstellen Sie immer zuerst einen Snapshot der Datenbank — `pg_dump` aus dem Postgres-
Container, oder verwenden Sie die anwendungsinterne Seite **Wartung → Backups** (Admin-UI),
die einen zeitgestempelten SQL-Dump in das per Bind-Mount eingebundene Verzeichnis `./backups/`
schreibt. Konfigurieren Sie in derselben UI einen täglichen Backup-Zeitplan, sodass der
Snapshot aktuell ist, wenn eine unerwartete Regression auftritt.

> **Pre-Flight-SSL-Prüfung** — führen Sie dies vor dem Pullen aus. Wenn eine der Dateien fehlt,
> startet der nginx-Container zwar, liefert aber keinen HTTPS-Verkehr.
> ```bash
> cd /opt/ipsolis
> ls -la nginx/ssl/cert.pem nginx/ssl/key.pem
> ```
> Falls sie fehlen, erzeugen Sie das Zertifikat neu (siehe Abschnitt 4), bevor Sie fortfahren.

Pullen Sie die neuen Images. **Produktion:** Erhöhen Sie `IPSOLIS_VERSION` auf das neue getestete Release.
**Pre-live / Test:** Lassen Sie es unbesetzt, um den neuesten Build zu pullen.

```bash
cd /opt/ipsolis
git pull origin main                  # refresh compose files / nginx.conf / docs
export IPSOLIS_VERSION=x.x.x          # production: set to the release you want (e.g. 0.6.12)
                                      # pre-live/test: leave unset to track :latest
export COMPOSE_FILE=docker-compose.ghcr.yml:docker-compose.prod.yml
docker compose pull                   # fetch the new images
docker compose up -d                  # recreate changed containers (no build)
docker compose exec -T api alembic upgrade head   # apply any new migrations
docker compose restart nginx          # pick up new container IPs / config
curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health | python3 -m json.tool
```

> Migrationen können gefahrlos mehrfach ausgeführt werden -- Alembic verfolgt, welche
> bereits angewendet wurden, und überspringt diese. Jede Feature-Einheit liefert
> typischerweise ihre eigene Migration; prüfen Sie zwischen Upgrades
> `api/alembic/versions/` auf den Changeset und `docker compose exec api alembic
> history`, um die Kette zu sehen.

### Beat-HA-Failover während des Neustarts

Wenn Sie mehrere Beat-Replicas (`--scale beat=N`) betreiben, rollt `docker compose
up -d` die Container nacheinander aus, und der Leader-Lock geht
innerhalb von ~13 s an die überlebende Replica über.
Bei Single-Beat-Installationen gibt es während des Neustarts eine kurze Lücke,
in der periodische Tasks nicht laufen — meist unsichtbar, da die Kadenzen
in Minuten / Stunden liegen.

---

## 12. Hochverfügbare Deployments (optional)

> **Optional — die meisten Deployments brauchen das nicht.** Eine Einzelinstanz von ip·Solis
> (die Standardeinrichtung aus den Abschnitten 1–7) ist für die große Mehrheit der
> Installationen die richtige Wahl. Ein kurzer Ausfall — z. B. während eines Upgrade-Neustarts
> — ist selten kritisch: laufende Vorgänge werden fortgesetzt und geplante Tasks laufen in
> Minuten-/Stunden-Kadenzen. Ziehen Sie die folgenden Muster nur in Betracht, wenn Sie eine
> konkrete Verfügbarkeitsanforderung (z. B. ein SLA) haben oder auf sehr hohe Auftragsvolumina
> skalieren. Andernfalls können Sie diesen Abschnitt bedenkenlos überspringen.

ip·Solis skaliert horizontal auf der API- und Worker-Ebene. Der Beat-Scheduler
unterstützt Multi-Replica-HA über celery-redbeat. Dieser Abschnitt behandelt die beiden
getesteten Skalierungsszenarien: API-Replicas und Worker-Replicas.

### 12.1 Multi-Replica-API

Die API ist **per Design zustandslos** — jede Replica behandelt jede
Anfrage gleichwertig, und es besteht kein Bedarf an Sticky-Session-Affinität am
Load Balancer.

**Was sie zustandslos macht**:

* Sessions verwenden Starlettes
  [`SessionMiddleware`](https://www.starlette.io/middleware/#sessionmiddleware)
  im Cookie-signierten Modus (`api/app/main.py`): Die gesamte Session-
  Payload (Admin-Benutzer-ID, Rolle, CSRF-Token) liegt im
  `xp_session`-Cookie selbst, signiert mit `API_SECRET_KEY`. Keine
  serverseitige Session-Tabelle.
* Tokenisierte URLs (`/approve/<token>`,
  `/portal/certifications/review/<token>` usw.) sind mit demselben
  `API_SECRET_KEY` HMAC-signiert und nur verifizierend. Keine Replay-Tabelle.
* Sämtlicher Anfragezustand liegt in Postgres oder Redis — beide über
  Replicas hinweg gemeinsam genutzt.

**Was jede Replica gemeinsam nutzen MUSS**:

| Was | Warum | Wie |
|---|---|---|
| `API_SECRET_KEY` | Signiert Session-Cookies + Genehmigungstokens. Unterschiedliche Schlüssel pro Replica = Clients sehen die Hälfte der Zeit "Session ungültig" / "Genehmigungslink abgelaufen". | In `.env` fixieren; über `env_file:` in Compose laden, sodass jede Replica dieselbe Datei liest. |
| `DATABASE_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Gemeinsame Postgres- + Redis-Backplane. | Wie oben. |
| Gemeinsame Dateisystem-Mounts | `licenses/`, `scripts/`, `backups/` sind per Bind-Mount eingebunden; Replicas, die dieselben Pfade lesen, müssen denselben Inhalt sehen. Auf einem einzelnen Host geschieht das automatisch. Auf mehreren Hosts verwenden Sie NFS / GlusterFS / einen gemeinsamen Volume-Treiber — oder migrieren Sie den relevanten Inhalt in S3-kompatiblen Objektspeicher (eine zurückgestellte Einheit). | Single-Host-Deployments benötigen keine zusätzliche Verkabelung. |

**Skalierungsbefehle**:

```bash
# Single-host: bump the api replica count via compose
docker compose -f docker-compose.ghcr.yml -f docker-compose.prod.yml \
  up -d --scale api=3

# Verify each replica is reachable through the load balancer
for i in 1 2 3; do
  curl -fsk https://YOUR_HOSTNAME.YOUR_COMPANY.COM/health \
    -H 'X-Replica-Probe: '$i
done
```

**Hinweise zur Load-Balancer-Konfiguration**:

* **Keine Sticky Sessions erforderlich**. Round-Robin oder Least-Connections
  ist in Ordnung.
* **Health-Check**: `GET /health` (unauthentifiziert). Gibt
  `{status: ok | degraded}` zurück und aggregiert die Liveness von Datenbank, Redis und Beat.
  Der Endpunkt ist schnell (ein Redis-Ping + ein
  DB SELECT 1), sodass ein LB-Prüfintervall von 5–10 s sicher ist.
* **TLS-Terminierung**: am Load Balancer belassen (oder am bestehenden
  nginx-Sidecar aus Abschnitt 5). Die Replicas liefern intern reines HTTP;
  das Flag
  [`https_only=True`](https://www.starlette.io/middleware/#sessionmiddleware)
  an der `SessionMiddleware` schützt das `Secure`-Bit des Cookies,
  unabhängig davon, wo TLS terminiert wird.

**Rollender Neustart während Upgrades**: Der Upgrade-Ablauf in Abschnitt 11
stoppt und startet jede Replica gemeinsam, was für kleine Flotten in Ordnung ist,
bei denen ~30 s API-Ausfallzeit akzeptabel sind. Für Rolls ohne Ausfallzeit
falten Sie den `up -d`-Schritt in eine Schleife pro Replica:

```bash
for i in 1 2 3; do
  docker compose stop api-$i
  docker compose -f docker-compose.ghcr.yml -f docker-compose.prod.yml \
    up -d --no-deps api-$i
  # Wait for the new container to pass health
  until curl -fsk http://localhost/health > /dev/null 2>&1; do
    sleep 2
  done
done
```

Dies erfordert einen LB, der ein Backend nach dem anderen drainen kann; beim
standardmäßigen Round-Robin-nginx-Upstream gehen In-Flight-Anfragen auf der
neu startenden Replica verloren. Die Drain-Logik liegt in der Verantwortung Ihres LB.

### 12.2 Multi-Replica-Worker

Celery-Worker sind zustandslose Consumer — sie ziehen aus den benannten
Redis-Queues und verarbeiten Tasks. Das Hinzufügen weiterer Worker ist ein einzeiliges
Scale-up; der Worker-Code selbst ändert sich nicht.

**Queue-Topologie** (definiert in `worker/tasks/__init__.py`):

| Queue | Tasks | Warum eine separate Queue |
|---|---|---|
| `provision` | Order-Workflows (`dynamic_runner`, `standalone_runner`, `ps_module_installer`, `sccm_probe`) — alles, was AD / SCCM / vSphere / XenServer berührt. | Provisionierungsschritte rufen PowerShell auf (~5–60 s/Schritt) und halten Verbindungen zu externen Systemen. Sie zu isolieren verhindert, dass ein langsamer vSphere-Aufruf schnelle Housekeeping-Tasks blockiert. |
| `notifications` | E-Mail-Versand, Teams-Kartenzustellung, Genehmigungserinnerungen, Zertifizierungserinnerungen, Kostenalarme. | I/O-gebunden, latenzempfindlich (ein hängender SMTP-Server sollte sich nicht hinter einer 30-s-SCCM-Probe stauen). |
| `default` | Audit-Retention-Prune, SIEM-Streaming, Lizenzprüfung, Update-Checker, Kostenbericht-Snapshot, DB-Backup, **API-Token-Purge**. | Hintergrund-Housekeeping. Größtenteils cron-gesteuert, niederfrequent. |
| `reclaim` | Asset-Ablaufprüfungen (`check_expiring_assets`). | Stündlicher Beat-Task; klein, aber isoliert, sodass der stündliche Tick nicht mit Order-Workflows um einen Worker-Slot konkurriert. |

**Dimensionierungsempfehlungen** (Concurrency pro Queue × Replica-Anzahl):

| Pool-Größe | Empfohlene Konfiguration | Begründung |
|---|---|---|
| Lab / Einzelteam (≤50 Benutzer) | 1 Worker-Replica, `--concurrency=4 -Q provision,notifications,default,reclaim` | Alle Queues auf einem Prozess; Concurrency 4 reicht für die typischen 1–2 Bestellungen/Stunde völlig aus. |
| Mittel (≤500 Benutzer, ≤20 Bestellungen/Stunde) | 2 Worker-Replicas, nach Queue aufgeteilt: Replica A `-Q provision --concurrency=4`, Replica B `-Q notifications,default,reclaim --concurrency=2` | Die Provisionierungslatenz bleibt durch Replica A begrenzt; Replica B übernimmt Housekeeping + Erinnerungen ohne Head-of-Line-Blocking der Queue. |
| Groß (≥500 Benutzer, ≥50 Bestellungen/Stunde, regulierte SLAs) | 3+ Worker-Replicas: dedizierte `provision`-Worker (`--concurrency=8` × 2 Replicas), eine `notifications`-Replica (`--concurrency=4`), eine `default,reclaim`-Replica (`--concurrency=2`) | Die Skalierung pro Queue passt zur tatsächlichen Lastform. |

**Skalierungsbefehl** (Single-Host, alle Queues auf jeder Replica):

```bash
docker compose -f docker-compose.ghcr.yml -f docker-compose.prod.yml \
  up -d --scale worker=3
```

**Dedizierte Replicas pro Queue** erfordern entweder separate Compose-
Service-Definitionen (z. B. `worker-provision`, `worker-notifications`),
jeweils mit eigenem `command:`, das die Standard-Queue-Liste überschreibt, oder
ein Laufzeit-`command:`-Override:

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

**Beat-Skalierung**: Der Beat-Container hat keinen festen `container_name`, sodass er
für HA repliziert werden kann:

```bash
docker compose \
  -f docker-compose.ghcr.yml \
  -f docker-compose.prod.yml \
  up -d --scale beat=2
```

> **Hinweis**: Celery Beat ist ein Singleton-Scheduler. Mehrere Beat-Replicas sind nur dann
> sinnvoll, wenn ein verteiltes Lock-Backend vorhanden ist — `celery-redbeat` (bereits konfiguriert) verwendet
> Redis-Locks, um doppeltes Auslösen von Tasks zu verhindern.

**Liveness**: Jeder Worker registriert sich beim Mingle-on-Startup von Celery,
was bedeutet, dass ein frischer Worker innerhalb weniger Sekunden für Beat / andere Worker
sichtbar ist. Es gibt keinen separaten Health-Check zu verdrahten — wenn
der Worker-Container `Up` ist, konsumiert er.

**Sichtbarkeit**: Flower (der bestehende `flower`-Dienst im Dev-
Compose; siehe `docker-compose.yml`) zeigt die Live-Worker-Registrierung,
die Queue-Tiefe und die Dauer-Aufschlüsselung pro Task. Setzen Sie ihm für die Produktion
dieselbe nginx-Authentifizierung wie der Admin-UI vor; Flower hat keine
eingebaute Authentifizierung über HTTP Basic hinaus.

### 12.3 Postgres-Hochverfügbarkeit

Postgres-HA (Streaming-Replikation, pgBackRest, Patroni) ist architektonisch
möglich — ip·Solis ist Single-Primary, und jeder Wechsel der Connection-String erfordert
lediglich eine Änderung an `.env` und einen Neustart. Eine validierte Schritt-für-Schritt-Anleitung ist in
dieser Version nicht enthalten.

---

## 13. Fehlerbehebung

### Container startet nicht

```bash
# Check container status and exit codes
docker compose ps -a

# Check logs for the failing service
docker compose logs <service-name> --tail=50
```

### Health-Check schlägt über nginx fehl, aber API ist gesund

Nginx hat möglicherweise die alte Container-IP zwischengespeichert. Starten Sie den Container neu
(nicht nur `nginx -s reload` — Docker-Bind-Mounts behalten sonst den alten Inode):

```bash
docker compose \
  -f docker-compose.ghcr.yml \
  -f docker-compose.prod.yml \
  restart nginx
```

### Datenbank-Verbindungsfehler

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

### AD-/LDAP-Verbindungsprobleme

1. Überprüfen Sie die Netzwerkverbindung vom Container aus:
   ```bash
   docker compose exec api curl -v telnet://dc01.yourcompany.com:389
   ```
2. Prüfen Sie die AD-Einstellungen unter Admin > Einstellungen
3. Prüfen Sie die API-Logs auf LDAP-Fehler:
   ```bash
   docker compose logs api 2>&1 | grep -i "ldap\|ad_lookup"
   ```

### E-Mails werden nicht versendet

1. Überprüfen Sie die SMTP-Einstellungen unter Admin > Einstellungen
2. Prüfen Sie die Worker-Logs auf SMTP-Fehler:
   ```bash
   docker compose logs worker 2>&1 | grep -i "smtp\|mail\|notification"
   ```
3. Stellen Sie sicher, dass der Server das SMTP-Relay erreichen kann:
   ```bash
   docker compose exec api curl -v telnet://smtp.yourcompany.com:587
   ```

### Zugriff auf SSL-Verzeichnis verweigert

```bash
sudo chmod 644 nginx/ssl/cert.pem
sudo chmod 600 nginx/ssl/key.pem
```

---

## 14. Sauberes Zurücksetzen (Testumgebungen)

> **Nur Test- und Staging-Umgebungen.** Dieser Abschnitt zerstört dauerhaft alle
> Daten. Niemals auf einer Produktionsinstanz ausführen.

Docker-Volumes (Datenbankdaten, Redis-Daten) überstehen `rm -rf /opt/ipsolis`, da
sie unter `/var/lib/docker/volumes/` gespeichert sind — unabhängig vom Repository-
Verzeichnis. Für eine vollständig saubere Neuinstallation:

```bash
# 1. Stop the stack and delete volumes
cd /opt/ipsolis
docker compose \
  -f docker-compose.ghcr.yml \
  -f docker-compose.prod.yml \
  down -v

# 2. Remove the repository directory
cd /opt
sudo rm -rf ipsolis

# 3. Reinstall (continue from section 2), then start the stack via section 6
sudo git clone https://github.com/XenPool/ipsolis.git ipsolis
sudo chown -R $USER:$USER ipsolis
cd ipsolis
```

Nach diesem Zurücksetzen enthält die Datenbank keine Benutzer, keine Konfiguration und keine Assets —
die Ersteinrichtung (Abschnitt 7) muss erneut durchgeführt werden.
