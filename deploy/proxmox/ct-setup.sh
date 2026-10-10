#!/usr/bin/env bash
# Läuft im Container (Debian 12/13). Wird von finanz-o-mat-lxc.sh aufgerufen.
#
#   ct-setup.sh install   Pakete, Benutzer, Python-Umgebung und systemd-Dienst einrichten
#   ct-setup.sh update    Datenbank sichern, neuesten Stand holen, neu installieren, Dienst neu starten
#                         (im Container auch als finanz-o-mat-update aufrufbar)
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
NODE_MIN=20
NODE_LINIE=22
NODE_DIR=/opt/node

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    exit 1
}
trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR

hat_systemd() { [[ -d /run/systemd/system ]]; }

env_wert() {
    # Wert einer Variable aus der Konfiguration, ohne Anführungszeichen
    sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}\$/\1/p" "$ENV_DATEI" | tail -n1
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
    install -d -o "$BENUTZER" -g "$BENUTZER" -m 750 "$DATEN" "$DATEN/sicherungen"
    install -d -m 755 /srv/rechnungen
    install -d -m 750 -g "$BENUTZER" "$(dirname "$ENV_DATEI")"
    if [[ ! -f $ENV_DATEI ]]; then
        # Ohne Vorgabe vom Host die Beispielkonfiguration mit Pfaden für den Container nehmen
        sed -e "s|^DATENBANK=.*|DATENBANK=$DATEN/finanzomat.sqlite|" \
            -e "s|^RECHNUNGS_VERZEICHNIS=.*|RECHNUNGS_VERZEICHNIS=/srv/rechnungen|" \
            "$APP_DIR/.env.example" >"$ENV_DATEI"
        warn "$ENV_DATEI aus .env.example angelegt, bitte anpassen"
    fi
    chown root:"$BENUTZER" "$ENV_DATEI"
    chmod 640 "$ENV_DATEI"
}

node_bereitstellen() {
    # Debian 13 bringt Node 20 mit; bei älteren Systemen die offizielle LTS-Version nach $NODE_DIR laden
    if [[ -x $NODE_DIR/bin/node ]]; then
        export PATH="$NODE_DIR/bin:$PATH"
        return 0
    fi
    local kandidat
    kandidat=$(apt-cache policy nodejs 2>/dev/null | awk '/Candidate:/ {print $2}' | sed 's/^[0-9]*://; s/\..*//')
    if [[ $kandidat =~ ^[0-9]+$ ]] && ((kandidat >= NODE_MIN)); then
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
        (cd "$FRONTEND" && npm ci --no-audit --no-fund --loglevel=error)
    else
        (cd "$FRONTEND" && npm install --no-audit --no-fund --loglevel=error)
    fi
    (cd "$FRONTEND" && npm run build --silent)
    ok "Oberfläche gebaut"
}

python_umgebung() {
    info "Installiere die App in $VENV"
    [[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
    # editierbar: Die App läuft direkt aus dem Checkout, gebaute Oberflächendateien sind so ohne Weiteres dabei
    "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade --editable "$APP_DIR"
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
    chmod 755 /usr/local/bin/finanz-o-mat /usr/local/bin/finanz-o-mat-update
}

dienst_starten() {
    if ! hat_systemd; then
        warn "Kein systemd aktiv, Dienst nicht gestartet"
        return 0
    fi
    systemctl daemon-reload
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

installieren() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."
    [[ -d $APP_DIR/.git ]] || abbruch "$APP_DIR ist kein Git-Checkout der App."
    pakete
    benutzer_und_ordner
    frontend_bauen
    python_umgebung
    dienst_dateien
    dienst_starten
}

aktualisieren() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."
    local zweig vorher nachher erzwingen=${1:-}
    cd "$APP_DIR"
    zweig=$(git rev-parse --abbrev-ref HEAD)
    vorher=$(git rev-parse --short HEAD)
    info "Hole neuesten Stand von $zweig"
    git fetch --quiet origin "$zweig"
    nachher=$(git rev-parse --short FETCH_HEAD)
    if [[ $vorher == "$nachher" && $erzwingen != --force ]]; then
        ok "Schon aktuell ($vorher). Neu installieren erzwingen: finanz-o-mat-update --force"
        return 0
    fi
    sichern
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
    ok "Update fertig"
}

main() {
    case ${1:-} in
        install) installieren ;;
        update) aktualisieren "${2:-}" ;;
        nach-update) nach_update ;;
        *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
    esac
}

main "$@"
exit
