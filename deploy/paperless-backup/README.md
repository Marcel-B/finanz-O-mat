# Paperless-ngx nach Nextcloud sichern

`paperless-backup.sh` sichert Paperless-ngx mit allen Dokumenten jede Nacht verschlüsselt in eine Nextcloud
(z. B. Hetzner Storage Share). Es läuft dort, wo Paperless läuft: im Paperless-Container aus dem
Proxmox-Helper-Script (`/opt/paperless`) oder auf dem Rechner mit der Docker-Installation.

Ablauf eines Laufs:

1. `document_exporter` schreibt Dokumente (Originale, Archiv-PDFs, Vorschaubilder) und alle Metadaten
   (Tags, Korrespondenten, Felder, Benutzer, Workflows …) in einen Exportordner. Das ist das Format, das Paperless
   selbst zum Umziehen und Zurückspielen vorsieht, und es ist unabhängig von Datenbank und Version. Der Ordner
   bleibt liegen und wird bei jedem Lauf nur abgeglichen, nicht neu geschrieben.
2. [restic](https://restic.net) sichert den Exportordner und die Paperless-Konfiguration (`paperless.conf` bzw.
   die Compose-Dateien) über [rclone](https://rclone.org) per WebDAV in die Nextcloud. Alles ist mit AES-256
   verschlüsselt, Hetzner sieht nur Datenblöcke. Unveränderte Dokumente werden nicht erneut hochgeladen.
3. Alte Stände werden ausgedünnt (Vorgabe: 7 tägliche, 5 wöchentliche, 12 monatliche, 10 jährliche) und eine
   Stichprobe von 2 % der Daten wird heruntergeladen und geprüft.
4. Bei einem Fehler kommt eine Nachricht (ntfy) und/oder die Überwachung (healthchecks.io) schlägt an.

Finanz-O-Mat sichert sich nach demselben Schema (restic über rclone, Ordner `Backups/finanz-o-mat`), siehe
[deploy/proxmox/README.md](../proxmox/README.md#backup-auf-die-nextcloud). Beide können dasselbe App-Passwort nutzen.

## Einrichten

Vorher in der Nextcloud ein App-Passwort anlegen: Profilbild > Persönliche Einstellungen > Sicherheit >
„Neues App-Passwort erstellen“ (Name z. B. `paperless-backup`). Das normale Passwort geht auch, ist aber nicht zu
empfehlen, weil sich ein App-Passwort einzeln widerrufen lässt.

Dann als root in der Shell von Paperless (beim Helper-Script: Proxmox > Container > Konsole, oder `pct enter <CTID>`
auf dem Host):

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/paperless-backup/paperless-backup.sh)" -- einrichten
```

Das Skript installiert `restic` und `rclone` (apt), erkennt die Paperless-Installation und fragt nach:

| Frage | Beispiel |
|---|---|
| Nextcloud-Adresse | `https://nx12345.your-storageshare.de` (oder gleich die WebDAV-Adresse aus Dateien > Dateien-Einstellungen) |
| Benutzername, App-Passwort | wie in der Nextcloud |
| Ordner in der Nextcloud | `Backups/paperless` |
| Uhrzeit | `02:30` |
| Aufbewahrung | `--keep-daily 7 --keep-weekly 5 --keep-monthly 12 --keep-yearly 10` |
| Ping-URL, ntfy-Thema | optional, siehe „Benachrichtigung“ |

Am Ende zeigt es das **restic-Passwort**. Das gehört sofort in den Passwortmanager, zusammen mit Nextcloud-Adresse,
Benutzer und Ordner. Ohne dieses Passwort lässt sich die Sicherung nicht mehr öffnen, auch nicht von Hetzner.

Danach läuft die erste Sicherung im Hintergrund. Sie lädt alles einmal hoch und kann je nach Datenmenge dauern;
verfolgen mit `journalctl -fu paperless-backup`. Die folgenden Läufe übertragen nur Neues.

Ohne Rückfragen geht es mit `-y`, die Werte kommen dann aus Umgebungsvariablen (`NC_URL`, `NC_BENUTZER`,
`NC_APP_PASSWORT`, `ZIEL_ORDNER`, `SICHERUNG_ZEIT`, `BEHALTEN`, `HC_PING_URL`, `NTFY_URL`, bei einer schon
vorhandenen Sicherung `RESTIC_PASSWORT`). Erneutes `paperless-backup einrichten` übernimmt die bisherigen Werte als
Vorgabe; so lassen sich Uhrzeit oder Zugangsdaten ändern.

**Platz:** Der Exportordner braucht ungefähr so viel Platz wie Paperless' `media/`-Ordner. Beim Helper-Script
liegt er unter `/var/backups/paperless-export` im selben Container; notfalls die Container-Platte vergrößern
(Proxmox > Container > Ressourcen > Laufwerk > Größe ändern) oder `EXPORT_DIR` auf eine andere Platte legen.
Mit `EXPORT_OPTIONEN="--no-thumbnail"` entfallen die Vorschaubilder.

**Docker:** Das Skript nimmt den laufenden Container mit dem Image `paperless-ngx` und exportiert in den Ordner,
der dort unter `/usr/src/paperless/export` eingebunden ist (Standard in der `docker-compose.yml`: `./export`),
und zwar in den Unterordner `paperless-backup/`. Gesichert werden zusätzlich die `*.yml`/`*.env`-Dateien im
Compose-Verzeichnis. Läuft Docker in einem LXC-Container, das Skript in diesem Container einrichten.

## Was wo liegt

| Pfad | Inhalt |
|---|---|
| `/usr/local/sbin/paperless-backup` | das Skript |
| `/etc/paperless-backup/paperless-backup.env` | Konfiguration |
| `/etc/paperless-backup/rclone.conf` | Nextcloud-Zugang (Passwort mit `rclone obscure` verschleiert) |
| `/etc/paperless-backup/restic-passwort` | Schlüssel der Sicherung |
| `/etc/systemd/system/paperless-backup.{service,timer}` | täglicher Lauf |
| `/var/backups/paperless-export` | Exportordner (Helper-Script/lokal) |
| `/var/cache/paperless-backup` | Cache von restic, darf gelöscht werden |
| Nextcloud: `Backups/paperless` | verschlüsselte Sicherung (restic-Repository) |

## Im Alltag

```sh
paperless-backup status                  # nächster Lauf, Ergebnis des letzten, letzte Sicherungen
paperless-backup liste                   # alle Sicherungen
systemctl start paperless-backup         # jetzt sichern (oder paperless-backup sichern, mit Ausgabe)
journalctl -u paperless-backup -n 50     # Protokoll
paperless-backup pruefen 100%            # alles einmal herunterladen und prüfen (ab und zu sinnvoll)
paperless-backup restic stats            # restic mit der hinterlegten Konfiguration
```

## Benachrichtigung

Ein Fehler steht immer im Journal. Damit er auch auffällt, eins oder beides in der Konfiguration eintragen
(oder bei `einrichten` angeben):

- `HC_PING_URL`: eine Prüfung bei [healthchecks.io](https://healthchecks.io) (kostenlos) mit Zeitplan „täglich“.
  Das Skript meldet Start, Erfolg und Fehler (mit den letzten Zeilen des Protokolls). Vorteil: Es meldet sich auch,
  wenn die Sicherung gar nicht läuft, z. B. weil der Container aus ist. Healthchecks schickt dann eine Mail.
- `NTFY_URL`: ein [ntfy](https://ntfy.sh)-Thema, z. B. `https://ntfy.sh/<langer-zufälliger-name>`, für eine
  Push-Nachricht aufs Handy bei Fehlern (`NTFY_BEI_ERFOLG="ja"` auch bei Erfolg, `NTFY_TOKEN` für ein geschütztes Thema).

## Aufbewahrung

Paperless selbst ist das Archiv; die Sicherung schützt vor Ausfall, Fehlbedienung und Verschlüsselungstrojanern.
Ein in Paperless gelöschtes Dokument steckt noch in den älteren Ständen: mit der Vorgabe bleibt der Stand vom
Monatsende ein Jahr und der vom Jahresende zehn Jahre erhalten (Aufbewahrungsfrist für Buchungsbelege). Weil restic nur Änderungen speichert, kosten die vielen Stände kaum Platz.

## Wiederherstellen

Probe aufs Exempel einmal nach der Einrichtung machen, z. B. in einem frischen Test-Container.

### 1. Sicherung holen

Auf dem Rechner mit eingerichtetem Skript (oder nach `paperless-backup einrichten` auf einem neuen; bei einer
vorhandenen Sicherung fragt es nach dem restic-Passwort):

```sh
paperless-backup liste                                  # Stand aussuchen, z. B. latest oder eine ID
paperless-backup wiederherstellen latest /var/tmp/paperless-wiederherstellung
```

Das legt die gesicherten Pfade unterhalb des Zielordners ab (z. B.
`/var/tmp/paperless-wiederherstellung/var/backups/paperless-export`) und zeigt die nächsten Schritte an.

Ganz ohne das Skript reichen restic, rclone und die Daten aus dem Passwortmanager:

```sh
apt install restic rclone
rclone config create nextcloud webdav vendor=nextcloud \
  url=https://nx12345.your-storageshare.de/remote.php/dav/files/<benutzer>/ user=<benutzer> pass=<app-passwort>
export RESTIC_REPOSITORY=rclone:nextcloud:Backups/paperless
restic snapshots                                        # fragt nach dem restic-Passwort
restic restore latest --target /var/tmp/paperless-wiederherstellung
```

### 2. In Paperless einspielen

`document_importer` erwartet ein frisch installiertes, leeres Paperless in **derselben Version** wie beim Export
(steht in `metadata.json` im Exportordner und wird beim Wiederherstellen angezeigt). Also Paperless neu installieren
(Helper-Script bzw. `docker compose up -d`), einmal starten, keine Dokumente hineinlegen, dann:

Helper-Script / lokal:

```sh
systemctl stop paperless-consumer paperless-task-queue paperless-scheduler
cd /opt/paperless/src
../.venv/bin/python manage.py document_importer /var/tmp/paperless-wiederherstellung/var/backups/paperless-export
# ältere Helper-Script-Installationen ohne .venv: python3 manage.py document_importer …
systemctl start paperless-consumer paperless-task-queue paperless-scheduler
```

Docker (im Compose-Verzeichnis, Exportordner `./export`):

```sh
cp -a /var/tmp/paperless-wiederherstellung/<pfad>/paperless-backup/. ./export/import/
docker compose exec -T webserver document_importer ../export/import
```

Danach sind Dokumente, Tags, Korrespondenten, Felder und Benutzer wieder da (Anmeldung mit den alten Zugangsdaten).
Aus der Sicherung bei Bedarf noch `paperless.conf` bzw. `docker-compose.env` übernehmen (Mail-Konten, OCR-Sprache,
`PAPERLESS_SECRET_KEY`), die Datenbank-Zugangsdaten aber von der neuen Installation lassen. Finanz-O-Mat braucht
danach evtl. ein neues API-Token (Einstellungen > Paperless).

Einzelne Dokumente gehen auch ohne Import: Die PDFs liegen im wiederhergestellten Exportordner als normale Dateien.

## Entfernen

```sh
systemctl disable --now paperless-backup.timer
rm /etc/systemd/system/paperless-backup.{service,timer} /usr/local/sbin/paperless-backup
rm -r /etc/paperless-backup /var/cache/paperless-backup /var/backups/paperless-export
```

Die Sicherung in der Nextcloud bleibt dabei liegen.
