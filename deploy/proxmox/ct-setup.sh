#!/usr/bin/env bash
# Läuft im Container (Debian 12/13). Wird von finanz-o-mat-lxc.sh aufgerufen.
#
#   ct-setup.sh install   Pakete, Benutzer, Python-Umgebung und systemd-Dienst einrichten
#   ct-setup.sh tailscale Tailscale aus der offiziellen Paketquelle installieren (braucht /dev/net/tun)
#   ct-setup.sh update    Datenbank sichern, neuesten Stand holen, neu installieren, Dienst neu starten
#                         (im Container auch als finanz-o-mat-update aufrufbar)
#   ct-setup.sh pruefen   nachsehen, ob es eine neue Version gibt (läuft per Timer), Ergebnis für die App
#
# Update aus der App: Die App legt /var/lib/finanz-o-mat/update/anfordern an, eine systemd-Path-Unit startet
# dann finanz-o-mat-update.service (als root). Ebenso startet .../update/pruefen sofort eine Prüfung
# („Nach Updates suchen“). Den Stand liest die App aus .../update/status.json.
#
# Backup auf eine Nextcloud: deploy/proxmox/ct-backup.sh (finanz-o-mat-backup), täglich per Timer, „Jetzt sichern“
# in der App über .../backup/anfordern wie beim Update.
#
# Konfiguration und Datenbank liegen außerhalb des Programmverzeichnisses und bleiben bei Updates erhalten:
#   /etc/finanz-o-mat/finanz-o-mat.env   Einstellungen (Paperless, Anmeldung, Versteuerung, ...)
#   /var/lib/finanz-o-mat/               SQLite-Datenbank und Sicherungen

set -Eeuo pipefail

APP_DIR=/opt/finanz-o-mat
VENV=$APP_DIR/.venv
ENV_DATEI=/etc/finanz-o-mat/finanz-o-mat.env
DATEN=/var/lib/finanz-o-mat
BENUTZER=finanzomat
DIENST=finanz-o-mat
SICHERUNGEN_BEHALTEN=10
FRONTEND=$APP_DIR/frontend
# Vite 8 braucht Node ^20.19 oder >= 22.12
NODE_MIN=20.19
NODE_LINIE=22
NODE_DIR=/opt/node
UPDATE_DIR=$DATEN/update
BACKUP_DIR=$DATEN/backup
SPERRE=/run/finanz-o-mat-update.lock

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    # In einer Subshell nur abbrechen; aufräumen macht die Hauptshell
    ((BASH_SUBSHELL == 0)) || exit 1
    if [[ -n ${UPDATE_LAEUFT:-} ]]; then
        unset UPDATE_LAEUFT
        # Zurück auf den bisherigen Stand, damit Code und laufende Version zusammenpassen
        if [[ -n ${VORHER_COMMIT:-} ]]; then
            git -C "$APP_DIR" reset --quiet --hard "$VORHER_COMMIT" || true
            hat_systemd && systemctl restart "$DIENST" || true
            printf '\e[1;33m !!\e[0m %s\n' "Zurück auf $(git -C "$APP_DIR" rev-parse --short HEAD)" >&2
        fi
        # Der App mitteilen, dass das Update schiefging
        status_schreiben fehler "$*" || true
    fi
    exit 1
}
trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR

hat_systemd() { [[ -d /run/systemd/system ]]; }

env_wert() {
    # Wert einer Variable aus der Konfiguration, ohne Anführungszeichen
    sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}\$/\1/p" "$ENV_DATEI" | tail -n1
}

status_schreiben() {
    # Stand für die App: installierte Version, neue Version (FETCH_HEAD) und die Commits dazwischen.
    # Texte gehen über die Umgebung an Python, damit Anführungszeichen in Commit-Texten nichts kaputt machen.
    local zustand=$1 meldung=${2:-} neu="" aenderungen="" installiert datum
    [[ -d $UPDATE_DIR ]] || return 0
    installiert=$(git -C "$APP_DIR" rev-parse --short HEAD)
    datum=$(git -C "$APP_DIR" log -1 --format=%cI HEAD)
    if git -C "$APP_DIR" rev-parse -q --verify FETCH_HEAD >/dev/null; then
        neu=$(git -C "$APP_DIR" rev-parse --short FETCH_HEAD)
        aenderungen=$(git -C "$APP_DIR" log --format=%s HEAD..FETCH_HEAD | head -n 30)
    fi
    ZUSTAND=$zustand MELDUNG=$meldung INSTALLIERT=$installiert DATUM=$datum NEU=$neu AENDERUNGEN=$aenderungen \
        AUTO=$(env_wert AUTO_UPDATE) python3 - "$UPDATE_DIR/status.json" <<'PY'
import json, os, sys
from datetime import datetime
e = os.environ
ziel = sys.argv[1]
daten = {
    "zustand": e["ZUSTAND"],
    "meldung": e["MELDUNG"],
    "installiert": e["INSTALLIERT"],
    "installiert_datum": e["DATUM"],
    "neu": e["NEU"] or None,
    "aenderungen": [z for z in e["AENDERUNGEN"].splitlines() if z.strip()],
    "geprueft": datetime.now().astimezone().isoformat(timespec="seconds"),
    "automatisch": e["AUTO"] == "ja",
}
with open(ziel + ".neu", "w", encoding="utf-8") as f:
    json.dump(daten, f, ensure_ascii=False, indent=1)
os.chmod(ziel + ".neu", 0o644)
os.replace(ziel + ".neu", ziel)
PY
}

pruefen() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."
    exec 9>"$SPERRE"
    # Läuft gerade ein Update, nicht dazwischenfunken
    flock -n 9 || return 0
    local zweig
    zweig=$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)
    if ! git -C "$APP_DIR" fetch --quiet origin "$zweig"; then
        status_schreiben unbekannt "GitHub nicht erreichbar"
        abbruch "git fetch fehlgeschlagen"
    fi
    if [[ $(git -C "$APP_DIR" rev-parse HEAD) == "$(git -C "$APP_DIR" rev-parse FETCH_HEAD)" ]]; then
        status_schreiben aktuell
        ok "Aktuell ($(git -C "$APP_DIR" rev-parse --short HEAD))"
        return 0
    fi
    status_schreiben neu
    ok "Neue Version: $(git -C "$APP_DIR" rev-parse --short FETCH_HEAD)"
    if [[ $(env_wert AUTO_UPDATE) == ja ]]; then
        info "AUTO_UPDATE=ja: Update wird angefordert"
        touch "$UPDATE_DIR/anfordern"
    fi
}

pakete() {
    info "Installiere Systempakete"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade >/dev/null
    apt-get install -y -qq --no-install-recommends python3 python3-venv git ca-certificates >/dev/null
    local version
    version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
        || abbruch "Python $version ist zu alt, die App braucht 3.11 oder neuer (Debian 12 oder neuer)."
    ok "Python $version"
}

benutzer_und_ordner() {
    if ! id "$BENUTZER" >/dev/null 2>&1; then
        useradd --system --home-dir "$DATEN" --shell /usr/sbin/nologin "$BENUTZER"
    fi
    install -d -o "$BENUTZER" -g "$BENUTZER" -m 750 "$DATEN" "$DATEN/sicherungen" "$UPDATE_DIR" "$BACKUP_DIR"
    install -d -m 755 /srv/rechnungen
    install -d -m 750 -g "$BENUTZER" "$(dirname "$ENV_DATEI")"
    if [[ ! -f $ENV_DATEI ]]; then
        # Ohne Vorgabe vom Host die Beispielkonfiguration mit Pfaden für den Container nehmen
        sed -e "s|^DATENBANK=.*|DATENBANK=$DATEN/finanzomat.sqlite|" \
            -e "s|^RECHNUNGS_VERZEICHNIS=.*|RECHNUNGS_VERZEICHNIS=/srv/rechnungen|" \
            "$APP_DIR/.env.example" >"$ENV_DATEI"
        warn "$ENV_DATEI aus .env.example angelegt, bitte anpassen"
    fi
    # Einstellungen, die ältere Installationen noch nicht haben
    if ! grep -q '^UPDATE_VERZEICHNIS=' "$ENV_DATEI"; then
        printf '\n# Update-Hinweis in der App\nUPDATE_VERZEICHNIS="%s"\n' "$UPDATE_DIR" >>"$ENV_DATEI"
    fi
    if ! grep -q '^AUTO_UPDATE=' "$ENV_DATEI"; then
        printf '# ja = neue Versionen automatisch installieren\nAUTO_UPDATE="nein"\n' >>"$ENV_DATEI"
    fi
    if ! grep -q '^BACKUP_VERZEICHNIS=' "$ENV_DATEI"; then
        printf '\n# Backup-Stand in der App (finanz-o-mat-backup)\nBACKUP_VERZEICHNIS="%s"\n' "$BACKUP_DIR" >>"$ENV_DATEI"
    fi
    chown root:"$BENUTZER" "$ENV_DATEI"
    chmod 640 "$ENV_DATEI"
}

node_bereitstellen() {
    # Debian 13 bringt Node 20.19 mit; bei älteren Systemen die offizielle LTS-Version nach $NODE_DIR laden
    if [[ -x $NODE_DIR/bin/node ]]; then
        export PATH="$NODE_DIR/bin:$PATH"
        return 0
    fi
    local kandidat
    kandidat=$(apt-cache policy nodejs 2>/dev/null | awk '/Candidate:/ {print $2}' | sed 's/^[0-9]*://')
    if [[ $kandidat =~ ^[0-9] ]] && dpkg --compare-versions "$kandidat" ge "$NODE_MIN"; then
        apt-get install -y -qq --no-install-recommends nodejs npm >/dev/null
        return 0
    fi
    local arch datei summen tmp
    case $(dpkg --print-architecture) in
        amd64) arch=x64 ;;
        arm64) arch=arm64 ;;
        *) abbruch "Keine Node.js-Version für $(dpkg --print-architecture) hinterlegt." ;;
    esac
    info "Lade Node.js $NODE_LINIE von nodejs.org"
    apt-get install -y -qq --no-install-recommends curl xz-utils >/dev/null
    summen=$(curl -fsSL "https://nodejs.org/dist/latest-v$NODE_LINIE.x/SHASUMS256.txt")
    datei=$(awk -v a="linux-$arch.tar.xz" '$2 ~ a"$" {print $2}' <<<"$summen")
    [[ -n $datei ]] || abbruch "Node.js-Paket für linux-$arch nicht gefunden."
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/$datei" "https://nodejs.org/dist/latest-v$NODE_LINIE.x/$datei"
    (cd "$tmp" && grep " $datei\$" <<<"$summen" | sha256sum -c --quiet -)
    install -d "$NODE_DIR"
    tar -xJf "$tmp/$datei" -C "$NODE_DIR" --strip-components=1
    rm -rf "$tmp"
    export PATH="$NODE_DIR/bin:$PATH"
}

frontend_bauen() {
    # Vue-Oberfläche (Vite) bauen, sobald es im Repo ein frontend/ gibt
    [[ -f $FRONTEND/package.json ]] || return 0
    node_bereitstellen
    info "Baue die Oberfläche mit Node $(node --version)"
    if [[ -f $FRONTEND/package-lock.json ]]; then
        npm --prefix "$FRONTEND" ci --no-audit --no-fund --loglevel=error || abbruch "npm ci fehlgeschlagen"
    else
        npm --prefix "$FRONTEND" install --no-audit --no-fund --loglevel=error || abbruch "npm install fehlgeschlagen"
    fi
    npm --prefix "$FRONTEND" run build --silent || abbruch "Bau der Oberfläche fehlgeschlagen"
    ok "Oberfläche gebaut"
}

python_umgebung() {
    info "Installiere die App in $VENV"
    [[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
    # editierbar: Die App läuft direkt aus dem Checkout, gebaute Oberflächendateien sind so ohne Weiteres dabei
    "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade --editable "$APP_DIR" \
        || abbruch "Installation der Python-Pakete fehlgeschlagen"
    ok "$("$VENV/bin/pip" show finanz-o-mat | sed -n 's/^Version: /finanz-o-mat /p')"
}

dienst_dateien() {
    cat >/etc/systemd/system/$DIENST.service <<EOF
[Unit]
Description=Finanz-O-Mat (UStVA)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$BENUTZER
Group=$BENUTZER
WorkingDirectory=$DATEN
Environment=PORT=8000
EnvironmentFile=$ENV_DATEI
ExecStart=$VENV/bin/python -m ustva serve --host 0.0.0.0 --port \${PORT}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

    # Kommandozeile mit derselben Konfiguration, z. B. finanz-o-mat bericht --einlesen
    cat >/usr/local/bin/finanz-o-mat <<EOF
#!/bin/sh
set -a
. $ENV_DATEI
set +a
cd $DATEN
exec runuser -u $BENUTZER -- $VENV/bin/finanz-o-mat "\$@"
EOF
    cat >/usr/local/bin/finanz-o-mat-update <<EOF
#!/bin/sh
exec bash $APP_DIR/deploy/proxmox/ct-setup.sh update "\$@"
EOF
    cat >/usr/local/bin/finanz-o-mat-backup <<EOF
#!/bin/sh
exec bash $APP_DIR/deploy/proxmox/ct-backup.sh "\$@"
EOF
    chmod 755 /usr/local/bin/finanz-o-mat /usr/local/bin/finanz-o-mat-update /usr/local/bin/finanz-o-mat-backup
    # Manche Shells im Container (z. B. über die Proxmox-Konsole) haben /usr/local/bin nicht im PATH
    local befehl
    for befehl in finanz-o-mat finanz-o-mat-update finanz-o-mat-backup; do
        ln -sfn "/usr/local/bin/$befehl" "/usr/bin/$befehl"
    done
    ok "Befehle: finanz-o-mat, finanz-o-mat-update, finanz-o-mat-backup"

    # Regelmäßig nach neuen Versionen sehen
    cat >/etc/systemd/system/$DIENST-pruefen.service <<EOF
[Unit]
Description=Finanz-O-Mat: nach neuer Version sehen
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $APP_DIR/deploy/proxmox/ct-setup.sh pruefen
# Anforderung aus der App erst am Ende entfernen, solange zeigt die App die Prüfung als laufend
ExecStopPost=/bin/rm -f $UPDATE_DIR/pruefen
EOF
    cat >/etc/systemd/system/$DIENST-pruefen.timer <<EOF
[Unit]
Description=Finanz-O-Mat: stündlich nach neuer Version sehen

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF
    # Update auf Anforderung aus der App (Datei anfordern), läuft als root
    cat >/etc/systemd/system/$DIENST-update.service <<EOF
[Unit]
Description=Finanz-O-Mat aktualisieren

[Service]
Type=oneshot
ExecStartPre=/bin/rm -f $UPDATE_DIR/anfordern
ExecStart=/bin/bash $APP_DIR/deploy/proxmox/ct-setup.sh update
TimeoutStartSec=30min
EOF
    # „Nach Updates suchen“ aus der App (Datei pruefen)
    cat >/etc/systemd/system/$DIENST-pruefen.path <<EOF
[Unit]
Description=Finanz-O-Mat: Prüfung auf Anforderung aus der App

[Path]
PathExists=$UPDATE_DIR/pruefen
Unit=$DIENST-pruefen.service

[Install]
WantedBy=multi-user.target
EOF
    cat >/etc/systemd/system/$DIENST-update.path <<EOF
[Unit]
Description=Finanz-O-Mat: Update-Anforderung aus der App

[Path]
PathExists=$UPDATE_DIR/anfordern
Unit=$DIENST-update.service

[Install]
WantedBy=multi-user.target
EOF

    # Tägliches Backup auf die Nextcloud (ohne Einrichtung meldet es nur „nicht eingerichtet“)
    cat >/etc/systemd/system/$DIENST-backup.service <<EOF
[Unit]
Description=Finanz-O-Mat: Backup auf die Nextcloud
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStartPre=/bin/rm -f $BACKUP_DIR/anfordern
ExecStart=/bin/bash $APP_DIR/deploy/proxmox/ct-backup.sh jetzt
TimeoutStartSec=2h
EOF
    cat >/etc/systemd/system/$DIENST-backup.timer <<EOF
[Unit]
Description=Finanz-O-Mat: tägliches Backup

[Timer]
OnCalendar=*-*-* 02:30
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
    # „Jetzt sichern“ aus der App (Datei anfordern)
    cat >/etc/systemd/system/$DIENST-backup.path <<EOF
[Unit]
Description=Finanz-O-Mat: Backup auf Anforderung aus der App

[Path]
PathExists=$BACKUP_DIR/anfordern
Unit=$DIENST-backup.service

[Install]
WantedBy=multi-user.target
EOF
}

dienst_starten() {
    if ! hat_systemd; then
        warn "Kein systemd aktiv, Dienst nicht gestartet"
        return 0
    fi
    systemctl daemon-reload
    systemctl enable --quiet --now "$DIENST-pruefen.timer" "$DIENST-pruefen.path" "$DIENST-update.path" \
        "$DIENST-backup.timer" "$DIENST-backup.path"
    systemctl enable --quiet "$DIENST"
    systemctl restart "$DIENST"
    pruefe_erreichbar
}

pruefe_erreichbar() {
    local port
    port=$(env_wert PORT)
    port=${port:-8000}
    for _ in $(seq 1 30); do
        # 200, oder 401 wenn eine Anmeldung eingerichtet ist
        if "$VENV/bin/python" - "$port" <<'PY' 2>/dev/null; then
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/status", timeout=2)
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code == 401 else 1)
PY
            ok "Dienst läuft auf Port $port"
            return 0
        fi
        sleep 1
    done
    journalctl -u "$DIENST" -n 30 --no-pager || true
    abbruch "Dienst antwortet nicht auf Port $port"
}

sichern() {
    local db ziel
    db=$(env_wert DATENBANK)
    db=${db:-$DATEN/finanzomat.sqlite}
    [[ -f $db ]] || return 0
    ziel="$DATEN/sicherungen/finanzomat-$(date +%Y%m%d-%H%M%S).sqlite"
    # Online-Sicherung über die SQLite-API, funktioniert auch bei laufendem Dienst
    "$VENV/bin/python" - "$db" "$ziel" <<'PY'
import sqlite3, sys
quelle, ziel = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with ziel:
    quelle.backup(ziel)
quelle.close()
ziel.close()
PY
    chown "$BENUTZER:$BENUTZER" "$ziel"
    ok "Datenbank gesichert: $ziel"
    # alte Sicherungen aufräumen
    find "$DATEN/sicherungen" -name 'finanzomat-*.sqlite' -printf '%f\n' | sort -r \
        | tail -n +$((SICHERUNGEN_BEHALTEN + 1)) | while read -r alt; do rm -f "$DATEN/sicherungen/$alt"; done
}

tailscale_installieren() {
    [[ -c /dev/net/tun ]] || abbruch "/dev/net/tun fehlt im Container. Auf dem Host: finanz-o-mat-lxc.sh tailscale <CTID>"
    if ! command -v tailscale >/dev/null; then
        local codename
        codename=$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release)
        export DEBIAN_FRONTEND=noninteractive
        apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null
        # offizielle Paketquelle von Tailscale, Updates kommen danach über apt
        curl -fsSL "https://pkgs.tailscale.com/stable/debian/$codename.noarmor.gpg" \
            -o /usr/share/keyrings/tailscale-archive-keyring.gpg
        curl -fsSL "https://pkgs.tailscale.com/stable/debian/$codename.tailscale-keyring.list" \
            -o /etc/apt/sources.list.d/tailscale.list
        apt-get update -qq
        apt-get install -y -qq tailscale >/dev/null
    fi
    if hat_systemd; then
        systemctl enable --now --quiet tailscaled
    fi
    ok "$(tailscale version | head -n1 | sed 's/^/Tailscale /')"
}

tailscale_aktualisieren() {
    command -v tailscale >/dev/null || return 0
    # Ein Problem mit der Tailscale-Paketquelle soll das App-Update nicht abbrechen
    if DEBIAN_FRONTEND=noninteractive apt-get update -qq \
        && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --only-upgrade tailscale >/dev/null; then
        ok "$(tailscale version | head -n1 | sed 's/^/Tailscale /')"
    else
        warn "Tailscale-Update fehlgeschlagen, später mit apt-get upgrade nachholen"
    fi
}

installieren() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."
    [[ -d $APP_DIR/.git ]] || abbruch "$APP_DIR ist kein Git-Checkout der App."
    pakete
    benutzer_und_ordner
    frontend_bauen
    python_umgebung
    dienst_dateien
    dienst_starten
    pruefen || warn "Versionsprüfung fehlgeschlagen"
}

aktualisieren() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."
    local zweig vorher nachher erzwingen=${1:-}
    # Nur ein Update gleichzeitig; die Sperre bleibt über das exec unten hinweg bestehen
    exec 9>"$SPERRE"
    flock -w 1800 9 || abbruch "Ein anderes Update läuft noch."
    rm -f "$UPDATE_DIR/anfordern"
    export UPDATE_LAEUFT=1
    status_schreiben laeuft
    cd "$APP_DIR"
    zweig=$(git rev-parse --abbrev-ref HEAD)
    vorher=$(git rev-parse --short HEAD)
    info "Hole neuesten Stand von $zweig"
    git fetch --quiet origin "$zweig"
    nachher=$(git rev-parse --short FETCH_HEAD)
    if [[ $vorher == "$nachher" && $erzwingen != --force ]]; then
        ok "Schon aktuell ($vorher). Neu installieren erzwingen: finanz-o-mat-update --force"
        unset UPDATE_LAEUFT
        status_schreiben aktuell
        return 0
    fi
    sichern
    VORHER_COMMIT=$(git rev-parse HEAD)
    export VORHER_COMMIT
    git reset --quiet --hard FETCH_HEAD
    ok "$vorher -> $nachher"
    # Neu einlesen, damit Änderungen an diesem Skript selbst schon für die Installation gelten
    exec bash "$APP_DIR/deploy/proxmox/ct-setup.sh" nach-update
}

nach_update() {
    benutzer_und_ordner
    frontend_bauen
    python_umgebung
    dienst_dateien
    dienst_starten
    tailscale_aktualisieren
    unset UPDATE_LAEUFT
    status_schreiben aktuell
    ok "Update fertig"
}

main() {
    case ${1:-} in
        install) installieren ;;
        update) aktualisieren "${2:-}" ;;
        nach-update) nach_update ;;
        tailscale) tailscale_installieren ;;
        pruefen) pruefen ;;
        *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
    esac
}

main "$@"
exit
