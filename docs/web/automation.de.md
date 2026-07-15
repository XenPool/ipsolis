---
title: Automatisierung & Runbooks
slug: automation
order: 3
description: Runbook-Editor, PowerShell-Schritte, Automatisierungsstrategien, Parameter-Geltungsbereiche (lokal/global/Kontext), eigenständige und Cron-Runbooks, PowerShell-Modul-Store und globale Variablen.
---

# Automatisierung & Runbooks

ip·Solis automatisiert IT-Vorgänge über eine Runbook-Engine, die auf PowerShell und Celery basiert. Runbooks definieren geordnete Abfolgen von Schritten, die ausgeführt werden, wenn ein Auftrag bereitgestellt, geändert oder zurückgenommen wird. Eigenständige Runbooks erweitern dies auf Ad-hoc- und geplante Vorgänge, die an keinen Asset-Typ gebunden sind.

---

## Automatisierungsstrategien

Jeder Asset-Typ wird mit einer von drei Automatisierungsstrategien konfiguriert, die bestimmen, wie Bereitstellung und Rücknahme ausgeführt werden.

### Gruppenzugriff

ip·Solis fügt den Benutzer zu einer oder mehreren **Active-Directory**-Gruppen hinzu oder entfernt ihn daraus. Es ist kein PowerShell-Scripting erforderlich. Konfigurieren Sie die Gruppenziele im Asset-Typ unter **Targets**.

Jedes Ziel legt fest:
- **Type** — AD-Gruppe. *(Entra-ID-Gruppen- und RDS-Collection-Ziele sind geplant — coming soon; heute wird nur die AD-Gruppe ausgeführt.)*
- **Identifier** — der Gruppen-DN (unterstützt den Platzhalter `{asset_name}`, der zur Bereitstellungszeit durch das zugewiesene Asset ersetzt wird)
- **Principal source** — welche Nutzer der Bestellung zur Gruppe hinzugefügt werden: der Antragsteller, die zusätzlichen RDP-Nutzer, die Admin-Nutzer oder alle

### Runbook

Ein vollständig skriptgesteuerter Workflow. Die Schritte werden der Reihe nach von einem Celery-Worker ausgeführt. Jeder Schritt ruft ein benanntes Skriptmodul (ein in der Datenbank gespeichertes PowerShell-Skript) mit konfigurierbaren Parametern auf.

Fehler in einem beliebigen Schritt brechen das Runbook ab und setzen den Auftrag auf `failed`, wobei die Fehlerausgabe des Schritts im Protokoll erscheint.

### Composite

Kombiniert sowohl Gruppenzugriffs- als auch Runbook-Schritte in einer definierten Abfolge. Schritte des Typs `GROUP_TARGETS` und `RUNBOOK` werden in der angegebenen Reihenfolge verschachtelt. Verwenden Sie dies für Workflows, die sowohl AD-Gruppenmanipulation als auch benutzerdefinierte PowerShell-Vorgänge benötigen.

---

## Runbook-Editor

Asset-Typ-Runbooks werden unter **Admin → Asset Definitions → [Typ] → Runbooks** konfiguriert.

![Runbook-Schritt-Editor](./screenshots/admin-runbook-editor.png)

Jede Runbook-Definition ist auf eine **Aktion** beschränkt:
- `provision` — wird ausgeführt, wenn ein Auftrag genehmigt wird und die Bereitstellung beginnt
- `modify` — wird ausgeführt, wenn ein Benutzer die Attribute eines aktiven Auftrags ändert
- `deprovision` — wird ausgeführt, wenn ein Auftrag zurückgegeben wird, abläuft oder widerrufen wird

### Schritte hinzufügen und anordnen

Schritte werden aus der Modul-Registry hinzugefügt. Jeder Schritt legt fest:
- **Module** — das aufzurufende Skriptmodul
- **Parameters** — Werte, die dem `param()`-Block des Moduls in PowerShell zugeordnet sind. Jeder Parameter wird entweder an eine **Kontextvariable** (Auftrags-, Asset- oder Benutzerdaten, die zur Laufzeit aufgelöst werden) oder an einen **literalen Wert** (eine feste Zeichenkette, die direkt in das Feld eingegeben wird) gebunden

Schritte können mit dem Ziehgriff (`☰`) oder den Tastaturschaltflächen ↑/↓ neu angeordnet werden.

### Nachverfolgung der Schrittausführung

Jede Schrittausführung wird aufgezeichnet mit:
- Start- und Endzeitstempeln
- Strukturierter JSON-Ausgabe aus dem stdout des PowerShell-Skripts
- Fehlerausgabe, falls der Schritt fehlgeschlagen ist

Die Auftragsdetailseite in der Admin-UI zeigt für jeden Auftrag ein einklappbares Schrittprotokoll an.

---

## Skriptmodule

Skriptmodule sind die Bausteine von Runbooks — benannte PowerShell-Skripte, die in der Datenbank gespeichert und als Runbook-Schritte aufrufbar sind.

Der integrierte Skript-Editor unter **Admin → Script Modules** unterstützt:
- Schreiben und Bearbeiten von PowerShell-Skripten mit einem `param()`-Block
- Parameter-Introspektion — ip·Solis parst den `param()`-Block, um Parameternamen und -typen anzuzeigen
- Kategorisierung nach Präfix (z. B. `SCCM - Delete Device` → Kategorie `sccm`)
- Export auf die Festplatte zur git-Nachverfolgung (`POST /admin/seed/export`)

**Skriptanforderungen:**
- JSON auf stdout zurückgeben
- Reines ASCII verwenden (keine Unicode-Zeichen)
- Nicht auf interaktive Eingabeaufforderungen angewiesen sein

### Parameterschema und „Parse from Script"

Jedes Skriptmodul hat ein **Parameterschema** — eine strukturierte Liste der Parameter, die das Skript akzeptiert (Name, Typ, Pflichtkennzeichen, optionaler Standardwert). Das Schema ist das, was der Runbook-Schritt-Editor liest, um die Benutzeroberfläche für die Parameterbindung aufzubauen: eine Zeile pro Parameter, mit einem Typ-Badge und einem Pflichtindikator.

Sie definieren das Schema manuell über die Parametertabelle unterhalb des Editors oder lassen es ip·Solis automatisch ableiten, indem Sie auf **↻ Parse from script** klicken. Diese Schaltfläche sendet den aktuellen Skripttext an den Server, der den PowerShell-`param()`-Block liest und jeden deklarierten Parameter extrahiert — seinen Namen, die Typannotation, das Kennzeichen `[Parameter(Mandatory=$true)]` und den Standardwert. Bestehende Zeilen werden an Ort und Stelle aktualisiert; neue Parameter werden hinzugefügt; Parameter, die aus dem Skript entfernt wurden, bleiben unberührt (sodass Sie sie bei Bedarf manuell entfernen können).

Unterstützte PowerShell-Typen werden auf vier kanonische Typen abgebildet, die von der Benutzeroberfläche verwendet werden:

| PowerShell-Typ | Schematyp |
|---|---|
| `[string]`, `[datetime]`, `[PSCredential]` | `string` |
| `[int]`, `[int32]`, `[int64]`, `[long]` | `int` |
| `[bool]`, `[switch]` | `bool` |
| `[hashtable]`, `[array]`, `[object]` | `json` |

---

## Parameter-Geltungsbereiche

Skripte und Runbook-Schritt-Bindungen arbeiten mit drei unterschiedlichen Variablen-Geltungsbereichen. Die Auswahl **§ Insert variable** im Skript-Editor gruppiert sie visuell.

### Lokale Parameter (PARAMS)

Lokale Parameter sind die im eigenen `param()`-Block des Skripts deklarierten Parameter. Sie repräsentieren die Eingaben des Skripts — die Werte, die die Runbook-Schritt-Bindung bei jeder Ausführung des Skripts bereitstellen muss.

Innerhalb des Skripts stehen lokale Parameter sowohl als ihre deklarierten PowerShell-Variablen (`$VMName`, `$UserEmail`, …) als auch über die injizierte `$PARAMS`-Hashtabelle (`$PARAMS.VMName`) zur Verfügung. Die `$PARAMS`-Form ist nützlich, wenn Parameternamen dynamisch sind oder wenn der gesamte Satz an eine Hilfsfunktion übergeben wird.

Im Runbook-Schritt-Editor erscheint jeder lokale Parameter als Bindungszeile. Sie wählen, ob Sie ihn als **literalen Wert** (eine feste Zeichenkette) bereitstellen oder einer zur Laufzeit aufgelösten **Kontextvariable** zuordnen.

### Globale Variablen (VARS)

Globale Variablen sind Schlüssel-Wert-Paare, die in der Datenbank unter **Admin → Global Variables** gespeichert sind. Sie stehen jedem Skript zur Verfügung, ohne explizit als Parameter übergeben zu werden, über die injizierte `$VARS`-Hashtabelle:

```powershell
$domain = $VARS.'ad.domain'
$server = $VARS.'sccm.server'
```

Verwenden Sie globale Variablen für Werte, die in vielen Skripten vorkommen, sich aber ändern können — Domänennamen, Serveradressen, Organisationscodes, gemeinsam genutzte Anmeldedaten. Das Ändern des Werts an einer Stelle aktualisiert ihn überall.

Zusätzlich zu benutzerdefinierten globalen Variablen stellt die `$VARS`-Hashtabelle auch Infrastruktur-Verbindungsschlüssel aus den Admin-Einstellungen bereit: `xenserver.host`, `xenserver.username`, `xenserver.password`, `vsphere.host`, `vsphere.username`, `vsphere.password`.

Als Secret typisierte globale Variablen werden verschlüsselt gespeichert. Ihre Werte werden nach der Erstellung nie in der Admin-UI angezeigt und erst zur Ausführungszeit im Worker entschlüsselt.

### Runbook-Kontextvariablen (CTX)

Kontextvariablen werden vom Runbook-Runner zur Ausführungszeit injiziert und repräsentieren den Live-Zustand des verarbeiteten Auftrags. Sie werden nicht im `param()`-Block des Skripts deklariert — der Runner übergibt sie zusammen mit den eigenen lokalen Parametern des Schritts, sodass sie ebenfalls über `$PARAMS` zugänglich sind:

```powershell
$assetName  = $PARAMS.asset_name
$userEmail  = $PARAMS.user_email
$orderId    = $PARAMS.order_id
$expiresAt  = $PARAMS.expires_at
```

Verfügbare Kontextvariablen:

| Schlüssel | Beschreibung |
|---|---|
| `asset_name` | Name des aus dem Pool ausgewählten Assets |
| `asset_id` | Datenbank-ID des Assets |
| `asset_type_name` | Name des Asset-Typs |
| `asset_type_id` | Datenbank-ID des Asset-Typs |
| `order_id` | Datenbank-ID des Auftrags |
| `requested_from` | Startdatum des Auftrags |
| `expires_at` | Ablaufdatum des Auftrags |
| `user_email` | E-Mail des anfragenden Benutzers |
| `user_name` | Anzeigename des anfragenden Benutzers |
| `owner_email` | E-Mail des Asset-Eigentümers (falls gesetzt) |
| `owner_name` | Anzeigename des Asset-Eigentümers |
| `rdp_users` | RDP-Benutzerliste (aus dem Auftragsformular) |
| `admin_users` | Admin-Benutzerliste (aus dem Auftragsformular) |
| `snow_req` | ServiceNow-REQ-Nummer (aus dem eingehenden Webhook) |
| `snow_ritm` | ServiceNow-RITM-Nummer (aus dem eingehenden Webhook) |

Im Runbook-Schritt-Editor werden Kontextvariablen im Dropdown **Context var** nach Kategorie gruppiert angeboten (Asset, Order, Users, XenServer, vSphere). Die Auswahl einer Variable entspricht dem Schreiben von `$PARAMS.<key>` im Skript.

---

## Eigenständige Runbooks *(Pro)*

Eigenständige Runbooks sind an keinen Asset-Typ gebunden. Sie sind nützlich für Wartungsaufgaben, einmalige Vorgänge, Massen-Benutzerverwaltung und geplante Wartungsjobs.

![Liste eigenständiger Runbooks](./screenshots/admin-standalone-runbook.png)

### Ad-hoc-Ausführung

Führen Sie ein eigenständiges Runbook sofort über **Admin → Standalone Runbooks → Run** aus. Die Ausführung wird mit einem Verlaufsprotokoll pro Lauf, strukturierter Schrittausgabe und einer optionalen Bedienernotiz nachverfolgt.

### Cron-Planung

Eigenständigen Runbooks kann ein Cron-Ausdruck zugewiesen werden. Die Celery-Beat-Aufgabe `dispatch-standalone-cron` läuft jede Minute und versendet Runbooks, deren Zeitplan ausgelöst wurde. Jeder Lauf wird im Verlauf des Runbooks aufgezeichnet.

Der Cron-Ausdruck folgt der Standard-UNIX-Syntax (Minute, Stunde, Tag des Monats, Monat, Wochentag). Beispiele:

| Ausdruck | Bedeutung |
|---|---|
| `0 2 * * *` | Täglich um 02:00 |
| `*/15 * * * *` | Alle 15 Minuten |
| `0 8 * * 1` | Jeden Montag um 08:00 |

---

## PowerShell-Modul-Store

ip·Solis verwaltet eine Registry von PowerShell-Modulen, die von Skriptmodulen geladen werden können, die im Worker-Container ausgeführt werden.

**Admin → Modules** ermöglicht Bedienern:
- **Install from PowerShell Gallery** — beliebige öffentliche PS-Gallery-Module suchen und installieren
- **Upload a custom module** — ein `.zip`-Archiv (gekapselter Modulordner) hochladen
- **Toggle Linux compatibility** — ein Modul als `Linux ✓`, `Windows only ✕` oder `Unverified ?` kennzeichnen

Der Worker führt PowerShell 7 unter Linux aus. Module, die nur mit `PSEdition_Desktop` gekennzeichnet sind, werden nicht geladen. Das Kompatibilitätskennzeichen hilft Bedienern dabei nachzuverfolgen, welche Module sicher in Schritten verwendet werden können, ohne dass ein externes Windows-PowerShell-Remoting-Ziel erforderlich ist.

Installierte Module werden in der Tabelle `ps_modules` gespeichert und stehen allen Skriptmodulen zur Verfügung.

---

## Globale Variablen

Globale Variablen sind Schlüssel-Wert-Paare, die in der Datenbank gespeichert und in Runbook-Schritt-Parameter injizierbar sind. Sie sind nützlich für Werte, die in vielen Runbooks vorkommen, sich aber im Laufe der Zeit ändern können — Domänennamen, Serveradressen, Organisationscodes.

Verwalten Sie globale Variablen unter **Admin → Global Variables**. Referenzieren Sie sie in Runbook-Schritt-Parametern als `{{var.my_variable_name}}`.

Als Secret typisierte Variablen werden verschlüsselt gespeichert und ihre Werte werden nach der Erstellung nie in der Admin-UI angezeigt.

---

## PowerShell-Ausführungsumgebung

Skripte laufen innerhalb des Celery-Worker-Containers (`ipsolis-worker`) unter Verwendung von `pwsh` (PowerShell 7 unter Linux). Der Worker übernimmt:

- **SSL-Zertifikat-Bypass** — global injiziert für Umgebungen mit selbstsignierten Zertifikaten (XenServer, vSphere, SCCM)
- **Unterdrückung interaktiver Eingabeaufforderungen** — stdin wird vorab beantwortet, um zu verhindern, dass Skripte bei Eingabeaufforderungen hängen bleiben
- **Stdout-Erfassung** — die JSON-Ausgabe des Skripts wird geparst und im Schrittprotokoll gespeichert

Skripte, die externe Systeme aufrufen (AD, vSphere, XenServer, SCCM), tun dies unter Verwendung der in `app_config` gespeicherten Anmeldedaten, nicht über `.env`. Das bedeutet, dass eine Rotation der Anmeldedaten nur eine Aktualisierung in den Admin-Einstellungen erfordert — kein Neuaufbau des Containers.

---

## Observability

- **OpenTelemetry-Tracing** — jede Celery-Aufgabe erzeugt einen Span, der mit dem Trace der ursprünglichen API-Anfrage verknüpft ist. Traces fließen zu jedem OTLP-kompatiblen Collector (Jaeger, Tempo, SigNoz, Honeycomb)
- **Schrittprotokolle** — verfügbar auf der Auftragsdetailseite in der Admin-UI für jede Runbook-Ausführung
- **Verlauf eigenständiger Läufe** — jeder Cron- oder Ad-hoc-Lauf erfasst Startzeit, Endzeit, Status pro Schritt und Bedienernotizen
