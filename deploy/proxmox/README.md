# Finanz-O-Mat in Proxmox VE (LXC)

`finanz-o-mat-lxc.sh` legt auf einem Proxmox-VE-Host einen unprivilegierten Debian-Container an, installiert die App
darin und startet sie als systemd-Dienst. Ohne Docker, der Container braucht etwa 512 MB RAM und 4 GB Platte.

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
| `CORES`, `MEMORY`, `SWAP`, `DISK` | 1, 512, 512, 4 | CPU-Kerne, RAM und Swap in MB, Platte in GB |
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
| `REPO_URL`, `REPO_BRANCH` | dieses Repo, `main` | Quelle der App |

## Was wo liegt (im Container)

| Pfad | Inhalt |
|---|---|
| `/etc/finanz-o-mat/finanz-o-mat.env` | Konfiguration, gleiche Variablen wie `.env.example` |
| `/var/lib/finanz-o-mat/finanzomat.sqlite` | Datenbank |
| `/var/lib/finanz-o-mat/sicherungen/` | Sicherung der Datenbank vor jedem Update (die letzten 10) |
| `/srv/rechnungen` | Belegordner vom Host (falls eingebunden) |
| `/opt/finanz-o-mat` | Programm (Git-Checkout mit Python-Umgebung in `.venv`) |

Konfiguration und Datenbank bleiben bei Updates unverändert. Der Container läuft in den normalen Proxmox-Backups
(vzdump) mit; der eingebundene Belegordner nicht, der gehört in die Sicherung des Hosts.

Konfiguration ändern:

```sh
pct enter <CTID>
nano /etc/finanz-o-mat/finanz-o-mat.env      # Werte in "…", ohne " \ $ `
systemctl restart finanz-o-mat
journalctl -u finanz-o-mat -f                # Log
```

Kennzahlen auf der Kommandozeile, z. B. per Cronjob im Container:

```sh
finanz-o-mat bericht --einlesen
```

## Aktualisieren

Auf dem Host:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/proxmox/finanz-o-mat-lxc.sh)" -- update <CTID>
```

oder im Container `finanz-o-mat-update`. Das sichert die Datenbank, holt den neuesten Stand des Zweigs, installiert
die App neu und startet den Dienst. Ist nichts Neues da, passiert nichts (`finanz-o-mat-update --force` installiert
trotzdem neu). Eine Sicherung zurückspielen: Dienst stoppen, Datei aus `sicherungen/` über `finanzomat.sqlite`
kopieren, Dienst starten.

## Belegordner und Rechte

Der Container ist unprivilegiert: Dateien des Hosts erscheinen darin als `nobody`. Damit die App sie lesen kann,
müssen sie für alle lesbar sein:

```sh
chmod -R o+rX /tank/rechnungen
```

Ein Ordner lässt sich auch nachträglich einbinden:

```sh
pct set <CTID> --mp0 /tank/rechnungen,mp=/srv/rechnungen,ro=1
pct reboot <CTID>
```

Liegen die Rechnungen auf einem NAS, die Freigabe (SMB/NFS) auf dem Host einhängen und diesen Pfad einbinden.

## Erreichbarkeit

Die App lauscht im Container auf allen Adressen. Sie ist für das Heimnetz gedacht; nicht ohne Weiteres ins Internet
freigeben. Für HTTPS einen Reverse Proxy davorsetzen (z. B. Nginx Proxy Manager oder Caddy). Die Anmeldung
(`APP_BENUTZER`/`APP_PASSWORT`) ist HTTP Basic Auth und ohne HTTPS im Netz mitlesbar.
