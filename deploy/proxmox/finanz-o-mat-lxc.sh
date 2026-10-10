#!/usr/bin/env bash
# Finanz-O-Mat in einem Proxmox-LXC-Container installieren oder aktualisieren.
# Läuft in der Shell des Proxmox-VE-Hosts (als root), Aufruf siehe --help und deploy/proxmox/README.md.

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/Marcel-B/finanz-O-mat.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
APP_DIR=/opt/finanz-o-mat
ENV_DATEI=/etc/finanz-o-mat/finanz-o-mat.env
BELEG_CT_PATH=/srv/rechnungen

NICHT_FRAGEN="${NONINTERACTIVE:-0}"

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    exit 1
}
trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR

# So soll der Nutzer das Skript wieder aufrufen; bei "bash -c \"$(curl ...)\"" gibt es keine Datei
if [[ -f ${BASH_SOURCE[0]:-} ]]; then
    AUFRUF="bash $(realpath "${BASH_SOURCE[0]}")"
else
    AUFRUF="bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/proxmox/finanz-o-mat-lxc.sh)\" --"
fi

hilfe() {
    cat <<EOF
Finanz-O-Mat in einem Proxmox-LXC-Container installieren oder aktualisieren (auf dem PVE-Host als root).

Aufruf: $AUFRUF [-y | update <CTID> | tailscale <CTID>]

  (ohne)            neuen Container anlegen, fragt die wichtigsten Werte ab
  -y                ohne Rückfragen, Werte aus Umgebungsvariablen
  update <CTID>     App im Container aktualisieren (Datenbank wird vorher gesichert)
  tailscale <CTID>  Tailscale in einem bestehenden Container nachrüsten

Variablen (Vorgabe): CTID (nächste freie), CT_HOSTNAME (finanz-o-mat), CORES (1), MEMORY (1024), DISK (4),
BRIDGE (vmbr0), IP (dhcp), GATEWAY, CT_STORAGE, TEMPLATE_STORAGE, BELEG_HOST_PATH, PAPERLESS_URL,
PAPERLESS_TOKEN, APP_BENUTZER (admin), APP_PASSWORT (zufällig), VERSTEUERUNG (ist), BUNDESLAND (NW),
EIGENE_USTID, PORT (8000), TAILSCALE (ja), TAILSCALE_AUTHKEY, TAILSCALE_SERVE (nein), REPO_URL, REPO_BRANCH (main). Details: deploy/proxmox/README.md
EOF
}

# frage VAR "Text" [geheim]: fragt einen Wert ab, Vorgabe ist der aktuelle Inhalt von VAR
frage() {
    local var=$1 text=$2 geheim=${3:-} eingabe
    [[ $NICHT_FRAGEN == 1 ]] && return 0
    if [[ -n $geheim ]]; then
        local hinweis="leer lassen"
        [[ -n ${!var} ]] && hinweis="Enter = behalten"
        read -rsp "$text ($hinweis): " eingabe
        echo
    else
        read -rp "$text [${!var}]: " eingabe
    fi
    [[ -n $eingabe ]] && printf -v "$var" '%s' "$eingabe"
    return 0
}

# Werte landen in einer Datei, die systemd und die Shell gleichermaßen lesen; Sonderzeichen würden das brechen.
pruefe_wert() {
    local name=$1 wert=$2
    [[ $wert != *[\"\\\$\`]* && $wert != *$'\n'* ]] \
        || abbruch "$name darf keine Anführungszeichen, Backslashes, \$ oder \` enthalten"
}

zufall() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-20}" || true; }

pruefe_host() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root in der Proxmox-Shell ausführen."
    if ! command -v pct >/dev/null || ! command -v pveam >/dev/null; then
        abbruch "pct/pveam nicht gefunden. Das Skript muss auf einem Proxmox-VE-Host laufen."
    fi
}

storage_mit() {
    # Name des ersten aktiven Speichers mit dem Inhaltstyp $1, bevorzugt $2
    local inhalt=$1 bevorzugt=${2:-} liste
    liste=$(pvesm status -content "$inhalt" 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
    if [[ -n $bevorzugt ]] && grep -qx "$bevorzugt" <<<"$liste"; then
        echo "$bevorzugt"
    else
        head -n1 <<<"$liste"
    fi
}

waehle_vorlage() {
    local version name
    info "Suche Debian-Vorlage"
    pveam update >/dev/null 2>&1 || warn "pveam update fehlgeschlagen, nutze vorhandene Liste"
    for version in ${DEBIAN_VERSION:-13 12}; do
        name=$(pveam available --section system 2>/dev/null | awk '{print $2}' \
            | grep -E "^debian-${version}-standard_.*amd64" | sort -V | tail -n1 || true)
        if [[ -n $name ]]; then
            VORLAGE=$name
            break
        fi
    done
    [[ -n ${VORLAGE:-} ]] || abbruch "Keine Debian-Vorlage gefunden (pveam available --section system)."
    if pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$VORLAGE"; then
        ok "Vorlage $VORLAGE schon vorhanden"
    else
        info "Lade $VORLAGE nach $TEMPLATE_STORAGE"
        pveam download "$TEMPLATE_STORAGE" "$VORLAGE" >/dev/null
    fi
}

im_ct() { pct exec "$CTID" -- env DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@"; }

warte_auf_netz() {
    local _
    for _ in $(seq 1 30); do
        if im_ct getent hosts github.com >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    abbruch "Container $CTID hat kein Netz (DNS für github.com schlägt fehl). Bridge/IP prüfen."
}

schreibe_env() {
    # Erzeugt die Konfiguration für den Container. Später im Container unter $ENV_DATEI änderbar.
    local datei=$1
    cat >"$datei" <<EOF
# Finanz-O-Mat – Konfiguration (angelegt $(date +%F) vom Proxmox-Skript)
# Nach Änderungen: systemctl restart finanz-o-mat
# Werte in doppelten Anführungszeichen, ohne " \\ \$ und \`.

# soll = Rechnungsdatum zählt, ist = Zahlungseingang zählt
VERSTEUERUNG="$VERSTEUERUNG"
DAUERFRISTVERLAENGERUNG="$DAUERFRISTVERLAENGERUNG"
# Bundesland des Finanzamts, für Feiertage
BUNDESLAND="$BUNDESLAND"
EIGENE_USTID="$EIGENE_USTID"

# Belegordner mit ausgang/ und eingang/ (vom Host eingebunden, nur lesend)
RECHNUNGS_VERZEICHNIS="$BELEG_CT_PATH"

# Paperless-ngx (Token unter Profil > API-Token)
PAPERLESS_URL="$PAPERLESS_URL"
PAPERLESS_TOKEN="$PAPERLESS_TOKEN"
PAPERLESS_TAGS_AUSGANG="Ausgangsrechnung"
PAPERLESS_TAGS_EINGANG="Eingangsrechnung"

EZB_KURSE="true"

# Anmeldung für die Weboberfläche
APP_BENUTZER="$APP_BENUTZER"
APP_PASSWORT="$APP_PASSWORT"

# Datenbank und Port im Container
DATENBANK="/var/lib/finanz-o-mat/finanzomat.sqlite"
PORT="$PORT"
EOF
}

tun_freigeben() {
    # Tailscale braucht /dev/net/tun; unprivilegierte Container bekommen es nur über die LXC-Konfiguration
    local conf=${PVE_LXC_DIR:-/etc/pve/lxc}/$CTID.conf
    [[ -f $conf ]] || abbruch "$conf nicht gefunden."
    grep -q 'dev/net/tun' "$conf" && return 0
    cat >>"$conf" <<EOF
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF
}

tailscale_einrichten() {
    info "Installiere Tailscale"
    im_ct bash "$APP_DIR/deploy/proxmox/ct-setup.sh" tailscale
    if im_ct tailscale status >/dev/null 2>&1; then
        ok "Tailscale ist schon angemeldet"
    elif [[ -n ${TAILSCALE_AUTHKEY:-} ]]; then
        im_ct tailscale up --auth-key "$TAILSCALE_AUTHKEY" --hostname "$CT_HOSTNAME"
    elif [[ $NICHT_FRAGEN != 1 ]]; then
        info "Tailscale-Anmeldung: den folgenden Link im Browser öffnen und das Gerät freigeben"
        im_ct tailscale up --hostname "$CT_HOSTNAME"
    else
        warn "Tailscale ist installiert, aber nicht angemeldet: pct exec $CTID -- tailscale up"
        return 0
    fi
    ok "Tailscale verbunden: $(im_ct tailscale ip -4 2>/dev/null | head -n1)"
    if [[ ${TAILSCALE_SERVE:-nein} == ja ]]; then
        # HTTPS mit gültigem Zertifikat unter https://<name>.<tailnet>.ts.net
        if im_ct timeout 60 tailscale serve --bg "$PORT" >/dev/null; then
            ok "HTTPS im Tailnet: $(im_ct tailscale serve status 2>/dev/null | grep -o 'https://[^ ]*' | head -n1)"
        else
            warn "tailscale serve fehlgeschlagen. In der Tailscale-Admin-Konsole unter DNS MagicDNS und HTTPS-Zertifikate aktivieren, dann: pct exec $CTID -- tailscale serve --bg $PORT"
        fi
    fi
}

installieren() {
    pruefe_host

    CTID="${CTID:-$(pvesh get /cluster/nextid)}"
    CT_HOSTNAME="${CT_HOSTNAME:-finanz-o-mat}"
    CORES="${CORES:-1}"
    MEMORY="${MEMORY:-1024}"
    SWAP="${SWAP:-512}"
    DISK="${DISK:-4}"
    BRIDGE="${BRIDGE:-vmbr0}"
    IP="${IP:-dhcp}"
    GATEWAY="${GATEWAY:-}"
    TIMEZONE="${TIMEZONE:-Europe/Berlin}"
    CT_STORAGE="${CT_STORAGE:-$(storage_mit rootdir local-lvm)}"
    TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(storage_mit vztmpl local)}"
    BELEG_HOST_PATH="${BELEG_HOST_PATH:-}"
    PAPERLESS_URL="${PAPERLESS_URL:-}"
    PAPERLESS_TOKEN="${PAPERLESS_TOKEN:-}"
    APP_BENUTZER="${APP_BENUTZER:-admin}"
    APP_PASSWORT="${APP_PASSWORT:-}"
    VERSTEUERUNG="${VERSTEUERUNG:-ist}"
    DAUERFRISTVERLAENGERUNG="${DAUERFRISTVERLAENGERUNG:-false}"
    BUNDESLAND="${BUNDESLAND:-NW}"
    EIGENE_USTID="${EIGENE_USTID:-}"
    PORT="${PORT:-8000}"
    TAILSCALE="${TAILSCALE:-ja}"
    TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
    TAILSCALE_SERVE="${TAILSCALE_SERVE:-nein}"

    [[ -n $CT_STORAGE ]] || abbruch "Kein Speicher für Container gefunden (CT_STORAGE setzen)."
    [[ -n $TEMPLATE_STORAGE ]] || abbruch "Kein Speicher für Vorlagen gefunden (TEMPLATE_STORAGE setzen)."

    echo
    info "Finanz-O-Mat im Proxmox-Container installieren"
    frage CTID "Container-ID"
    frage CT_HOSTNAME "Hostname"
    frage IP "IP-Adresse (dhcp oder z. B. 192.168.1.50/24)"
    if [[ $IP != dhcp ]]; then
        frage GATEWAY "Gateway"
    fi
    frage BELEG_HOST_PATH "Belegordner auf dem Host (leer = keiner, z. B. /tank/rechnungen)"
    frage PAPERLESS_URL "Paperless-URL (leer = kein Paperless, z. B. http://192.168.1.20:8000)"
    if [[ -n $PAPERLESS_URL ]]; then
        frage PAPERLESS_TOKEN "Paperless-API-Token" geheim
    fi
    frage BUNDESLAND "Bundesland des Finanzamts"
    frage EIGENE_USTID "Eigene USt-IdNr."
    frage VERSTEUERUNG "Versteuerung (ist/soll)"
    frage APP_BENUTZER "Benutzername für die Weboberfläche"
    frage APP_PASSWORT "Passwort für die Weboberfläche" geheim
    frage TAILSCALE "Tailscale einrichten (ja/nein)"
    if [[ $TAILSCALE == ja ]]; then
        frage TAILSCALE_AUTHKEY "Tailscale-Auth-Key (leer = Anmeldung über einen Link im Browser)" geheim
        frage TAILSCALE_SERVE "Zusätzlich HTTPS über tailscale serve (ja/nein)"
    fi

    [[ $CTID =~ ^[0-9]+$ ]] || abbruch "Container-ID muss eine Zahl sein."
    if pct status "$CTID" >/dev/null 2>&1; then
        abbruch "Container $CTID existiert schon. Für ein Update: $AUFRUF update $CTID"
    fi
    [[ $VERSTEUERUNG == ist || $VERSTEUERUNG == soll ]] || abbruch "VERSTEUERUNG muss ist oder soll sein."
    [[ $TAILSCALE == ja || $TAILSCALE == nein ]] || abbruch "TAILSCALE muss ja oder nein sein."
    [[ $TAILSCALE_SERVE == ja || $TAILSCALE_SERVE == nein ]] || abbruch "TAILSCALE_SERVE muss ja oder nein sein."
    [[ $IP == dhcp || $IP =~ ^[0-9.]+/[0-9]+$ ]] || abbruch "IP muss dhcp oder Adresse/Präfix sein."
    if [[ -n $BELEG_HOST_PATH && ! -d $BELEG_HOST_PATH ]]; then
        abbruch "Belegordner $BELEG_HOST_PATH gibt es auf dem Host nicht."
    fi
    PASSWORT_ERZEUGT=0
    if [[ -n $APP_BENUTZER && -z $APP_PASSWORT ]]; then
        APP_PASSWORT=$(zufall 20)
        PASSWORT_ERZEUGT=1
    fi
    for v in PAPERLESS_URL PAPERLESS_TOKEN APP_BENUTZER APP_PASSWORT EIGENE_USTID BUNDESLAND; do
        pruefe_wert "$v" "${!v}"
    done

    local netz="name=eth0,bridge=$BRIDGE,ip=$IP"
    [[ -n $GATEWAY ]] && netz+=",gw=$GATEWAY"

    echo
    info "Zusammenfassung"
    echo "  Container:   $CTID ($CT_HOSTNAME), $CORES CPU, $MEMORY MB RAM, $DISK GB auf $CT_STORAGE"
    echo "  Netz:        $BRIDGE, $IP${GATEWAY:+, Gateway $GATEWAY}"
    echo "  Belegordner: ${BELEG_HOST_PATH:-keiner} -> $BELEG_CT_PATH (nur lesend)"
    echo "  Paperless:   ${PAPERLESS_URL:-nicht genutzt}"
    echo "  Tailscale:   $TAILSCALE${TAILSCALE_AUTHKEY:+ (mit Auth-Key)}, HTTPS über tailscale serve: $TAILSCALE_SERVE"
    echo "  App:         $REPO_URL ($REPO_BRANCH), Port $PORT, $VERSTEUERUNG-Versteuerung"
    if [[ $NICHT_FRAGEN != 1 ]]; then
        read -rp "Container so anlegen? [J/n] " antwort
        [[ ${antwort:-j} =~ ^[JjYy]$ ]] || abbruch "Abgebrochen."
    fi

    waehle_vorlage

    info "Lege Container $CTID an"
    pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$VORLAGE" \
        --hostname "$CT_HOSTNAME" \
        --cores "$CORES" --memory "$MEMORY" --swap "$SWAP" \
        --rootfs "$CT_STORAGE:$DISK" \
        --net0 "$netz" \
        --unprivileged 1 --features nesting=1 \
        --onboot 1 --ostype debian --timezone "$TIMEZONE" \
        --tags finanz-o-mat \
        --description "Finanz-O-Mat (UStVA). Update auf dem Host: finanz-o-mat-lxc.sh update $CTID" \
        >/dev/null
    if [[ -n $BELEG_HOST_PATH ]]; then
        pct set "$CTID" --mp0 "$BELEG_HOST_PATH,mp=$BELEG_CT_PATH,ro=1"
    fi
    [[ $TAILSCALE == ja ]] && tun_freigeben
    ok "Container angelegt"

    info "Starte Container"
    pct start "$CTID"
    warte_auf_netz
    ok "Netz da"

    info "Installiere git und lade die App"
    im_ct apt-get update -qq
    im_ct apt-get install -y -qq git ca-certificates >/dev/null
    im_ct git clone --quiet --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"

    local tmp
    tmp=$(mktemp)
    schreibe_env "$tmp"
    im_ct mkdir -p "$(dirname "$ENV_DATEI")"
    pct push "$CTID" "$tmp" "$ENV_DATEI" --perms 600
    rm -f "$tmp"

    info "Richte Python-Umgebung und Dienst ein (dauert ein paar Minuten)"
    im_ct bash "$APP_DIR/deploy/proxmox/ct-setup.sh" install
    [[ $TAILSCALE == ja ]] && tailscale_einrichten

    local adresse ts_ip=""
    adresse=$(im_ct hostname -I 2>/dev/null | awk '{print $1}')
    echo
    if [[ $TAILSCALE == ja ]]; then
        ts_ip=$(im_ct tailscale ip -4 2>/dev/null | head -n1 || true)
    fi
    ok "Fertig. Finanz-O-Mat läuft unter http://${adresse:-<IP des Containers>}:$PORT"
    if [[ -n $ts_ip ]]; then
        echo "  Im Tailnet:    http://$CT_HOSTNAME:$PORT (MagicDNS) bzw. http://$ts_ip:$PORT"
    fi
    [[ -n $APP_BENUTZER ]] && echo "  Anmeldung: $APP_BENUTZER"
    if [[ $PASSWORT_ERZEUGT == 1 ]]; then
        echo "  Passwort:  $APP_PASSWORT   (erzeugt, bitte notieren)"
    fi
    echo "  Konfiguration: pct enter $CTID, dann $ENV_DATEI bearbeiten und systemctl restart finanz-o-mat"
    echo "  Update:        $AUFRUF update $CTID"
    if [[ -n $BELEG_HOST_PATH ]]; then
        echo "  Hinweis: Der Container ist unprivilegiert. Dateien im Belegordner müssen für alle lesbar sein (chmod -R o+rX)."
    fi
}

aktualisieren() {
    pruefe_host
    CTID=${1:-}
    [[ $CTID =~ ^[0-9]+$ ]] || abbruch "Aufruf: $AUFRUF update <CTID>"
    pct status "$CTID" 2>/dev/null | grep -q running || abbruch "Container $CTID läuft nicht."
    info "Aktualisiere Finanz-O-Mat in Container $CTID"
    im_ct /usr/local/bin/finanz-o-mat-update
}

tailscale_nachruesten() {
    # Tailscale in einem schon bestehenden Container einrichten
    pruefe_host
    CTID=${1:-}
    [[ $CTID =~ ^[0-9]+$ ]] || abbruch "Aufruf: $AUFRUF tailscale <CTID>"
    pct status "$CTID" >/dev/null 2>&1 || abbruch "Container $CTID gibt es nicht."
    CT_HOSTNAME=$(pct config "$CTID" | awk '/^hostname:/ {print $2}')
    CT_HOSTNAME=${CT_HOSTNAME:-finanz-o-mat}
    TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
    TAILSCALE_SERVE="${TAILSCALE_SERVE:-nein}"
    if ! grep -q 'dev/net/tun' "${PVE_LXC_DIR:-/etc/pve/lxc}/$CTID.conf"; then
        tun_freigeben
        info "Starte Container $CTID neu, damit /dev/net/tun verfügbar ist"
        pct reboot "$CTID"
    fi
    pct status "$CTID" | grep -q running || pct start "$CTID"
    warte_auf_netz
    PORT=$(im_ct sed -n 's/^PORT="\{0,1\}\([0-9]*\)"\{0,1\}$/\1/p' "$ENV_DATEI" 2>/dev/null || true)
    PORT=${PORT:-8000}
    tailscale_einrichten
}

main() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -y | --yes) NICHT_FRAGEN=1 ;;
            -h | --help) hilfe; exit 0 ;;
            install) ;;
            update) aktualisieren "${2:-}"; return ;;
            tailscale) tailscale_nachruesten "${2:-}"; return ;;
            *) abbruch "Unbekannte Option: $1 (siehe --help)" ;;
        esac
        shift
    done
    installieren
}

main "$@"
