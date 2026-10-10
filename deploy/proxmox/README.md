# Finanz-O-Mat in Proxmox VE (LXC)

`finanz-o-mat-lxc.sh` legt auf einem Proxmox-VE-Host einen unprivilegierten Debian-Container an, installiert die App
darin und startet sie als systemd-Dienst. Ohne Docker, der Container bekommt 1 GB RAM (für den Bau der Oberfläche) und
4 GB Platte.

## Installieren

In der Shell des Proxmox-Hosts (Weboberfläche: Knoten > Shell) als root:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/proxmox/finanz-o-mat-lxc.sh)"
```

Das Skript fragt nach Container-ID, IP-Adresse, Belegordner, Paperless-URL und -Token, Bundesland, USt-IdNr.,
Versteuerung und den Zugangsdaten für die Weboberfläche. Bleibt das Passwort leer, wird eins erzeugt und am Ende
angezeigt. Danach läuft die App unter `http://<IP des Containers>:8000`.

Ohne Rückfragen geht es mit `-y`; die Werte kommen dann aus Umgebungsvariablen:

```sh
CTID=120 IP=192.168.1.50/24 GATEWAY=192.168.1.1 \
BELEG_HOST_PATH=/tank/rechnungen \
PAPERLESS_URL=http://192.168.1.20:8000 PAPERLESS_TOKEN=... \
APP_PASSWORT=... EIGENE_USTID=DE... \
bash finanz-o-mat-lxc.sh -y
```

| Variable | Vorgabe | Bedeutung |
|---|---|---|
| `CTID` | nächste freie ID | Container-ID |
| `CT_HOSTNAME` | `finanz-o-mat` | Hostname |
| `CORES`, `MEMORY`, `SWAP`, `DISK` | 1, 1024, 512, 4 | CPU-Kerne, RAM und Swap in MB, Platte in GB |
| `BRIDGE`, `IP`, `GATEWAY` | `vmbr0`, `dhcp`, – | Netzwerk; feste IP als `192.168.1.50/24` |
| `CT_STORAGE`, `TEMPLATE_STORAGE` | `local-lvm`, `local` (sonst erster passender) | Speicher für Container und Vorlage |
| `DEBIAN_VERSION` | `13 12` | gesuchte Debian-Vorlage, erste gefundene gewinnt |
| `TIMEZONE` | `Europe/Berlin` | wichtig für Fristen und „heute“ |
| `BELEG_HOST_PATH` | – | Ordner auf dem Host mit `ausgang/` und `eingang/`, nur lesend unter `/srv/rechnungen` |
| `PAPERLESS_URL`, `PAPERLESS_TOKEN` | – | Paperless-ngx |
| `APP_BENUTZER`, `APP_PASSWORT` | `admin`, zufällig | Anmeldung an der Weboberfläche |
| `VERSTEUERUNG` | `ist` | `ist` oder `soll` |
| `BUNDESLAND`, `EIGENE_USTID` | `NW`, – | wie in `.env.example` |
| `PORT` | 8000 | Port im Container |
| `TAILSCALE` | `ja` | Tailscale im Container einrichten |
| `TAILSCALE_AUTHKEY` | – | Auth-Key; ohne Key zeigt das Skript einen Anmeldelink |
| `TAILSCALE_SERVE` | `nein` | zusätzlich HTTPS über `tailscale serve` |
| `AUTO_UPDATE` | `nein` | `ja` = neue Versionen automatisch installieren, sonst Hinweis in der App |
| `REPO_URL`, `REPO_BRANCH` | dieses Repo, `main` | Quelle der App |

## Was wo liegt (im Container)

| Pfad | Inhalt |
|---|---|
| `/etc/finanz-o-mat/finanz-o-mat.env` | Konfiguration, gleiche Variablen wie `.env.example` |
| `/var/lib/finanz-o-mat/finanzomat.sqlite` | Datenbank |
| `/var/lib/finanz-o-mat/sicherungen/` | Sicherung der Datenbank vor jedem Update (die letzten 10) |
| `/var/lib/finanz-o-mat/update/` | Stand der Update-Prüfung für die App (`status.json`) |
| `/var/lib/finanz-o-mat/backup/` | Stand des Nextcloud-Backups für die App (`status.json`) |
| `/etc/finanz-o-mat/backup.env`, `backup.passwort`, `rclone.conf` | Zugang zum Backup, nur für root lesbar |
| `/srv/rechnungen` | Belegordner vom Host (falls eingebunden) |
| `/opt/finanz-o-mat` | Programm (Git-Checkout mit Python-Umgebung in `.venv`, läuft direkt aus dem Checkout) |
| `/opt/node` | Node.js, nur falls das System kein Node 20.19 oder neuer mitbringt (Debian 12) |

Konfiguration und Datenbank bleiben bei Updates unverändert. Der Container läuft in den normalen Proxmox-Backups
(vzdump) mit; der eingebundene Belegordner nicht, der gehört in die Sicherung des Hosts. Zusätzlich gibt es ein
tägliches Backup außer Haus auf eine Nextcloud, siehe [Backup auf die Nextcloud](#backup-auf-die-nextcloud).

Konfiguration ändern:

```sh
pct enter <CTID>
nano /etc/finanz-o-mat/finanz-o-mat.env      # Werte in "…", ohne " \ $ `
systemctl restart finanz-o-mat
journalctl -u finanz-o-mat -f                # Log
finanz-o-mat passwort --zuruecksetzen        # Passwort vergessen (in der App gesetztes Passwort löschen)
```

Kennzahlen auf der Kommandozeile, z. B. per Cronjob im Container:

```sh
finanz-o-mat bericht --einlesen
```

## Aktualisieren

Der Container sieht stündlich auf GitHub nach, ob es auf dem Zweig (Standard `main`) einen neuen Stand gibt
(`finanz-o-mat-pruefen.timer`). Wenn ja, zeigt die App oben einen Hinweis mit den Änderungen und dem Knopf
„Jetzt aktualisieren“. Die App legt dafür nur eine Datei an; das Update selbst startet eine systemd-Path-Unit
als root (`finanz-o-mat-update.service`). Die Seite lädt danach von selbst neu. Mit `AUTO_UPDATE="ja"` in der
Konfiguration wird jede neue Version ohne Klick installiert. Unter „Einstellungen“ → „Version“ stehen die
installierte Version und der Zeitpunkt der letzten Prüfung; „Nach Updates suchen“ prüft sofort.

Schlägt ein Update fehl (z. B. der Bau der Oberfläche), geht das Programmverzeichnis auf den bisherigen Stand zurück,
die App zeigt einen Hinweis, Details stehen in `journalctl -u finanz-o-mat-update`.

Von Hand auf dem Host:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/proxmox/finanz-o-mat-lxc.sh)" -- update <CTID>
```

oder im Container `finanz-o-mat-update` (sofort nachsehen ohne Installation: `systemctl start finanz-o-mat-pruefen`, Ergebnis in
`/var/lib/finanz-o-mat/update/status.json`). Das sichert die Datenbank, holt den neuesten Stand des Zweigs, installiert
die App neu und startet den Dienst. Ist nichts Neues da, passiert nichts (`finanz-o-mat-update --force` installiert
trotzdem neu). Eine Sicherung zurückspielen: Dienst stoppen, Datei aus `sicherungen/` über `finanzomat.sqlite`
kopieren, Dienst starten.

## Backup auf die Nextcloud

Datenbank und Konfiguration gehen jede Nacht (gegen 2:30 Uhr) verschlüsselt auf eine Nextcloud, z. B. eine Hetzner
Storage Share. Das macht [restic](https://restic.net) über WebDAV (mit rclone); auf der Nextcloud liegen nur
verschlüsselte Blöcke. Es bleiben 30 tägliche, 12 monatliche und 10 jährliche Stände. Belege selbst liegen in
Paperless bzw. im Belegordner und gehören in deren Backup.

Einmal einrichten, im Container:

```sh
pct enter <CTID>
finanz-o-mat-backup einrichten
```

Das fragt nach der Nextcloud-Adresse (`https://nx12345.your-storageshare.de`), dem Benutzer und einem App-Passwort
(in der Nextcloud unter Persönliche Einstellungen > Sicherheit > „Neues App-Passwort erstellen“), legt den Ordner
`Backups/finanz-o-mat` an (neben `Backups/paperless` vom Paperless-Backup) und macht das erste Backup. Am Ende
zeigt es das **Backup-Passwort**: das in den Passwortmanager. Ohne es lassen sich die Backups nicht entschlüsseln,
auch nicht von Hetzner.

In der App steht unter „Einstellungen“ → „Backup“, wann das letzte Backup lief und wie viele Stände es gibt;
„Jetzt sichern“ startet eins sofort. Schlägt ein Backup fehl oder ist das letzte drei Tage oder älter, zeigt die App
oben eine Warnung. Log: `journalctl -u finanz-o-mat-backup`.

```sh
finanz-o-mat-backup                       # Backup sofort (wie der Timer)
finanz-o-mat-backup liste                 # vorhandene Stände
finanz-o-mat-backup ueberwachung URL      # Überwachung einschalten (ohne URL: fragt nach, „aus“ schaltet ab)
finanz-o-mat-backup wiederherstellen      # neuesten Stand zurückspielen (fragt nach)
finanz-o-mat-backup wiederherstellen 1a2b3c4d   # bestimmten Stand (ID aus liste)
```

Beim Zurückspielen wird die jetzige Datenbank vorher nach `sicherungen/vor-wiederherstellung-…` kopiert. Die
Konfiguration aus dem Backup landet zum Vergleich in `finanz-o-mat.env.aus-backup`; mit `--mit-konfig` ersetzt sie
die aktuelle.

Container weg (z. B. Platte defekt): neuen Container mit dem Installationsskript anlegen, dann darin
`finanz-o-mat-backup einrichten` mit denselben Nextcloud-Daten. Es erkennt das vorhandene Backup und fragt nach dessen
Backup-Passwort. Danach `finanz-o-mat-backup wiederherstellen --mit-konfig`.

Ohne Rückfragen: `BACKUP_URL`, `BACKUP_BENUTZER`, `BACKUP_APP_PASSWORT` und optional `BACKUP_ORDNER`,
`BACKUP_PASSWORT` (für ein vorhandenes Backup) und `HC_PING_URL` als Umgebungsvariablen setzen.

### Überwachung

Die Warnung in der App sieht nur, wer die App öffnet. Damit ein ausgefallenes Backup auch so auffällt, fragt
`einrichten` nach einer **Ping-URL** (später: `finanz-o-mat-backup ueberwachung <URL>`). Jedes Backup meldet dort
Start, Erfolg oder Fehler. Bleibt die Meldung aus, weil der Container aus ist oder das Heimnetz weg, schickt der
Dienst eine Mail.

- [healthchecks.io](https://healthchecks.io) (kostenlos, extern): Prüfung anlegen mit Period 1 Tag und Grace
  1 Stunde, die Ping-URL `https://hc-ping.com/<uuid>` eintragen. Eine eigene Prüfung je Backup (finanz-o-mat und
  Paperless), sonst überdeckt das eine Backup den Ausfall des anderen.
- Uptime Kuma (selbst gehostet): Monitor vom Typ „Push“ mit Heartbeat-Intervall 90000 Sekunden (25 Stunden), die
  Push-URL `https://<kuma>/api/push/<token>?…` eintragen. Läuft Kuma im selben Heimnetz, merkt es einen Ausfall des
  ganzen Netzes allerdings nicht.

## Oberfläche (Vue)

Gibt es im Repo ein `frontend/` mit `package.json` (Vue/Vite), baut das Skript die Oberfläche bei Installation und
jedem Update im Container (`npm ci` bzw. `npm install`, dann `npm run build`). Node.js kommt unter Debian 13 aus den
Paketquellen, unter Debian 12 als offizielle LTS-Version von nodejs.org (Prüfsumme wird kontrolliert). Die App läuft
direkt aus dem Checkout, daher werden gebaute Dateien ausgeliefert, egal wohin Vite sie im Repo schreibt.

## Belegordner und Rechte

Der Container ist unprivilegiert: Dateien des Hosts erscheinen darin als `nobody`. Damit die App sie lesen kann,
müssen sie für alle lesbar sein:

```sh
chmod -R o+rX /tank/rechnungen
```

Der Belegordner ist optional. Bleibt er bei der Installation leer, gibt es keinen Import aus einem Ordner; Belege
kommen dann aus Paperless oder werden in der Oberfläche eingegeben. Nachträglich einbinden:

```sh
pct set <CTID> --mp0 /tank/rechnungen,mp=/srv/rechnungen,ro=1
pct exec <CTID> -- sed -i 's|^RECHNUNGS_VERZEICHNIS=.*|RECHNUNGS_VERZEICHNIS="/srv/rechnungen"|' /etc/finanz-o-mat/finanz-o-mat.env
pct reboot <CTID>
```

Liegen die Rechnungen auf einem NAS, die Freigabe (SMB/NFS) auf dem Host einhängen und diesen Pfad einbinden.

## Tailscale

Standardmäßig kommt Tailscale mit in den Container, damit die App im eigenen Tailnet erreichbar ist, z. B. unter
`http://finanz-o-mat:8000` (mit MagicDNS) oder der 100.x-Adresse, die das Skript am Ende anzeigt.

- Anmeldung: Mit `TAILSCALE_AUTHKEY` (in der Admin-Konsole unter Settings > Keys erzeugen) läuft sie ohne Zutun.
  Ohne Key gibt das Skript einen Link aus; den im Browser öffnen und das Gerät freigeben.
- Das Skript gibt dem Container `/dev/net/tun` frei (zwei `lxc.`-Zeilen in `/etc/pve/lxc/<CTID>.conf`) und
  installiert Tailscale aus der offiziellen Paketquelle. Updates der App aktualisieren Tailscale mit.
- Mit `TAILSCALE_SERVE=ja` ist die App zusätzlich unter `https://finanz-o-mat.<tailnet>.ts.net` mit gültigem
  Zertifikat erreichbar. Dafür müssen in der Admin-Konsole unter DNS MagicDNS und HTTPS-Zertifikate aktiv sein.
- In einem schon angelegten Container nachrüsten (startet ihn einmal neu):

  ```sh
  bash finanz-o-mat-lxc.sh tailscale <CTID>
  ```

Den Key-Ablauf für den Container in der Admin-Konsole abschalten, sonst muss er sich nach 180 Tagen neu anmelden.

## Erreichbarkeit

Die App lauscht im Container auf allen Adressen, also im Heimnetz und im Tailnet. Nicht ins Internet freigeben.
Die Anmeldung (`APP_BENUTZER`/`APP_PASSWORT`) läuft über ein Formular mit Sitzungs-Cookie, Sperre nach
Fehlversuchen und Ablauf. Passwort und Paperless-Token lassen sich auch in der App unter „Einstellungen“ ändern; das
hat dann Vorrang vor der Env-Datei (Details und Passwort vergessen: „Einstellungen und Anmeldung“ im Haupt-README).
Die Verbindung ist im Tailnet ohnehin verschlüsselt, im Heimnetz ohne HTTPS mitlesbar. Wer HTTPS auch dort will,
nimmt `TAILSCALE_SERVE=ja` oder einen Reverse Proxy.
