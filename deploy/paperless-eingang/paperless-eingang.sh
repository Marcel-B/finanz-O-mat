#!/usr/bin/env bash
# Rechnungen automatisch nach Paperless-ngx holen:
#   1. Mail-Abruf: Paperless holt PDF-Anhänge aus den Rechnungsordnern des Postfachs (IMAP, z. B. Strato).
#   2. Eingangsordner: Dateien aus einem Nextcloud-Ordner (z. B. __unverarbeitet__) wandern in den Consume-Ordner.
# Läuft als root dort, wo Paperless läuft (Helper-Script-LXC oder Docker-Host). Aufruf siehe --help und README.md.

set -Eeuo pipefail

QUELLE_BASIS="${QUELLE_BASIS:-https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/paperless-eingang}"
KONF_DIR="${PAPERLESS_EINGANG_KONF:-/etc/paperless-eingang}"
ENV_DATEI=$KONF_DIR/paperless-eingang.env
RCLONE_KONF=$KONF_DIR/rclone.conf
BACKUP_RCLONE_KONF=/etc/paperless-backup/rclone.conf
PROGRAMM=/usr/local/sbin/paperless-eingang
LIB_DIR=/usr/local/lib/paperless-eingang
ZWISCHEN_DIR=/var/lib/paperless-eingang/abgeholt
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SPERRE=/run/paperless-eingang.lock
DOCKER_CONSUME=/usr/src/paperless/consume
# Unterordner, die bei "Unterordner als Tags" in der Nextcloud angelegt werden (Tags, die finanz-O-mat liest)
UNTERORDNER_VORSCHLAG="Eingangsrechnung Ausgangsrechnung Kontoauszug UStVA-Protokoll"

NICHT_FRAGEN=0

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    exit 1
}
trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR

hilfe() {
    cat <<EOF
Rechnungen automatisch nach Paperless-ngx holen (als root, dort wo Paperless läuft).

Aufruf: paperless-eingang <befehl>

  einrichten             beides einrichten (erneut = ändern/aktualisieren)
  mail                   nur den Mail-Abruf einrichten: Konto, Regeln je Rechnungsordner, Tags
  ordner [-y]            nur den Nextcloud-Ordner -> Consume-Ordner einrichten (Timer alle 2 Minuten)
  abholen                Dateien jetzt aus dem Nextcloud-Ordner holen (macht der Timer)
  status                 Timer, letzte Läufe, wartende Dateien
  rclone ...             rclone mit der hinterlegten Konfiguration aufrufen

Erstinstallation:
  bash -c "\$(curl -fsSL $QUELLE_BASIS/paperless-eingang.sh)" -- einrichten
Details: deploy/paperless-eingang/README.md im Repo finanz-O-mat
EOF
}

pruefe_root() { [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."; }

pruefe_wert() {
    local name=$1 wert=$2
    [[ $wert != *[\"\\\$\`]* && $wert != *$'\n'* ]] \
        || abbruch "$name darf keine Anführungszeichen, Backslashes, \$ oder \` enthalten"
}

frage() {
    local var=$1 text=$2 geheim=${3:-} eingabe
    [[ $NICHT_FRAGEN == 1 ]] && return 0
    if [[ -n $geheim ]]; then
        local hinweis="leer lassen"
        [[ -n ${!var:-} ]] && hinweis="Enter = behalten"
        read -rsp "$text ($hinweis): " eingabe
        echo
    else
        read -rp "$text [${!var:-}]: " eingabe
    fi
    [[ -n $eingabe ]] && printf -v "$var" '%s' "$eingabe"
    return 0
}

ja() {
    local antwort vorgabe=${2:-J}
    [[ $NICHT_FRAGEN == 1 ]] && { [[ $vorgabe == J ]]; return; }
    read -rp "$1 [$([[ $vorgabe == J ]] && echo J/n || echo j/N)]: " antwort
    antwort=${antwort:-$vorgabe}
    [[ $antwort == [JjYy]* ]]
}

lade_konfig() {
    [[ -f $ENV_DATEI ]] || abbruch "$ENV_DATEI fehlt. Erst einrichten: paperless-eingang ordner"
    set -a
    # shellcheck source=/dev/null
    . "$ENV_DATEI"
    set +a
    export RCLONE_CONFIG=$RCLONE_KONF
}

installiere_pakete() {
    local fehlt=() paket
    for paket in "$@"; do
        case $paket in
            flock) command -v flock >/dev/null || fehlt+=(util-linux) ;;
            *) command -v "$paket" >/dev/null || fehlt+=("$paket") ;;
        esac
    done
    [[ ${#fehlt[@]} -eq 0 ]] && return 0
    command -v apt-get >/dev/null || abbruch "Bitte ${fehlt[*]} installieren (kein apt-get gefunden)."
    info "Installiere ${fehlt[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${fehlt[@]}" >/dev/null
}

# --- Paperless finden -------------------------------------------------------------------------------------------

docker_container() {
    command -v docker >/dev/null || return 0
    { docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null || true; } | awk '$2 ~ /paperless-ngx/ {print $1; exit}'
}

erkenne_paperless() {
    # Setzt PAPERLESS_ART (lokal|docker), PAPERLESS_DIR bzw. PAPERLESS_CONTAINER und CONSUME_DIR, soweit noch leer.
    local container
    if [[ -z ${PAPERLESS_ART:-} || ${PAPERLESS_ART:-} == auto ]]; then
        container=$(docker_container)
        if [[ -n $container ]]; then
            PAPERLESS_ART=docker
            PAPERLESS_CONTAINER=${PAPERLESS_CONTAINER:-$container}
        elif [[ -f ${PAPERLESS_DIR:-/opt/paperless}/src/manage.py ]]; then
            PAPERLESS_ART=lokal
        else
            abbruch "Paperless nicht gefunden: weder ein laufender paperless-ngx-Container noch /opt/paperless/src/manage.py. PAPERLESS_ART und PAPERLESS_DIR bzw. PAPERLESS_CONTAINER setzen."
        fi
    fi
    case $PAPERLESS_ART in
        lokal)
            PAPERLESS_DIR=${PAPERLESS_DIR:-/opt/paperless}
            if [[ -z ${CONSUME_DIR:-} ]]; then
                CONSUME_DIR=$(paperless_conf_wert PAPERLESS_CONSUMPTION_DIR)
                CONSUME_DIR=${CONSUME_DIR:-$PAPERLESS_DIR/consume}
            fi
            ;;
        docker)
            [[ -n ${PAPERLESS_CONTAINER:-} ]] || PAPERLESS_CONTAINER=$(docker_container)
            [[ -n $PAPERLESS_CONTAINER ]] || abbruch "Kein laufender paperless-ngx-Container gefunden (PAPERLESS_CONTAINER setzen)."
            if [[ -z ${CONSUME_DIR:-} ]]; then
                CONSUME_DIR=$(docker inspect -f "{{range .Mounts}}{{if eq .Destination \"$DOCKER_CONSUME\"}}{{.Source}}{{end}}{{end}}" "$PAPERLESS_CONTAINER")
                [[ -n $CONSUME_DIR ]] || abbruch "Container $PAPERLESS_CONTAINER hat keinen Ordner unter $DOCKER_CONSUME eingebunden."
            fi
            ;;
        *) abbruch "PAPERLESS_ART muss lokal, docker oder auto sein, nicht '$PAPERLESS_ART'." ;;
    esac
    [[ -d $CONSUME_DIR ]] || abbruch "Consume-Ordner $CONSUME_DIR fehlt (CONSUME_DIR setzen)."
}

paperless_conf_wert() {
    # Wert einer Einstellung aus paperless.conf (nur Helper-Script/lokal), leer wenn nicht gesetzt
    local datei=${PAPERLESS_DIR:-/opt/paperless}/paperless.conf
    [[ -f $datei ]] || return 0
    sed -n "s/^[[:space:]]*$1=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}[[:space:]]*$/\1/p" "$datei" | tail -n 1
}

setze_zeile() {
    # setze_zeile DATEI NAME WERT: Zeile NAME=… ersetzen (auch auskommentierte) oder anhängen
    if grep -Eq "^[#[:space:]]*$2=" "$1"; then
        sed -i -E "s|^[#[:space:]]*$2=.*|$2=$3|" "$1"
    else
        printf '%s=%s\n' "$2" "$3" >>"$1"
    fi
}

python_fuer_paperless() {
    if [[ ${PAPERLESS_ART:-} == lokal && -x ${PAPERLESS_DIR:-/opt/paperless}/.venv/bin/python ]]; then
        echo "$PAPERLESS_DIR/.venv/bin/python"
    else
        installiere_pakete python3 >&2
        command -v python3
    fi
}

# --- Teil 1: Mail-Abruf -----------------------------------------------------------------------------------------

mailabruf_skript() {
    # Aus dem Checkout bzw. der Installation nehmen; beim Aufruf per curl frisch aus dem Repo laden
    local quelle=${BASH_SOURCE[0]:-}
    if [[ -f $quelle ]]; then
        if [[ -f $(dirname "$(realpath "$quelle")")/mailabruf.py ]]; then
            echo "$(dirname "$(realpath "$quelle")")/mailabruf.py"
            return
        elif [[ $(realpath "$quelle") == "$PROGRAMM" && -f $LIB_DIR/mailabruf.py ]]; then
            echo "$LIB_DIR/mailabruf.py"
            return
        fi
    fi
    mkdir -p "$LIB_DIR"
    curl -fsSL "$QUELLE_BASIS/mailabruf.py" -o "$LIB_DIR/mailabruf.py"
    echo "$LIB_DIR/mailabruf.py"
}

antwortet_paperless() {
    # Die API antwortet ohne Token mit 401/403, mit Weiterleitung oder 200 je nach Version
    local code
    code=$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$1/api/" 2>/dev/null || true)
    [[ $code =~ ^(200|301|302|401|403)$ ]]
}

finde_paperless_url() {
    # Setzt PAPERLESS_URL auf eine Adresse, unter der die Paperless-API auf diesem Rechner antwortet
    [[ -n ${PAPERLESS_URL:-} ]] && { export PAPERLESS_URL; return 0; }
    local ports=() port host url gesehen=" "
    # Helper-Script: GRANIAN_PORT im Dienst; sonst PAPERLESS_PORT; dazu die Ports, auf denen der Webserver lauscht
    port=$(systemctl show paperless-webserver.service -p Environment --value 2>/dev/null | tr ' ' '\n' \
        | sed -n 's/^\(GRANIAN_PORT\|PAPERLESS_PORT\)=//p' | head -n 1 || true)
    [[ -n $port ]] && ports+=("$port")
    port=$(paperless_conf_wert PAPERLESS_PORT)
    [[ -n $port ]] && ports+=("$port")
    if [[ $PAPERLESS_ART == docker ]]; then
        # z. B. "0.0.0.0:8000" -> 8000
        while read -r port; do ports+=("${port##*:}"); done < <(docker port "$PAPERLESS_CONTAINER" 8000/tcp 2>/dev/null || true)
    fi
    if command -v ss >/dev/null; then
        while read -r port; do ports+=("$port"); done < <(ss -Htlnp 2>/dev/null \
            | awk '/granian|gunicorn|uvicorn|python|docker-proxy/ {n = split($4, a, ":"); print a[n]}' | sort -un || true)
    fi
    ports+=(8000)
    for port in "${ports[@]}"; do
        [[ $gesehen == *" $port "* ]] && continue
        gesehen+="$port "
        for host in 127.0.0.1 ::1 $(hostname -I 2>/dev/null || true); do
            if [[ $host == *:* ]]; then url="http://[$host]:$port"; else url="http://$host:$port"; fi
            if antwortet_paperless "$url"; then
                PAPERLESS_URL=$url
                export PAPERLESS_URL
                ok "Paperless antwortet unter $url"
                return 0
            fi
        done
    done
    warn "Paperless antwortet auf diesem Rechner unter keiner der üblichen Adressen (Ports:${gesehen% })."
    warn "Läuft der Webserver? systemctl status paperless-webserver; offene Ports: ss -tlnp"
}

cmd_mail() {
    pruefe_root
    erkenne_paperless
    local python skript
    python=$(python_fuer_paperless)
    skript=$(mailabruf_skript)
    info "Mail-Abruf in Paperless einrichten"
    cat <<EOF
Paperless holt dann alle 10 Minuten PDF-Anhänge aus den Ordnern, die du gleich auswählst, und gibt ihnen die Tags
"Eingangsrechnung" und "E-Mail". Korrespondent und weitere Tags erkennt Paperless selbst. Gebraucht werden ein
API-Token aus Paperless (Profil > API-Auth-Token) und das Passwort des Postfachs.

EOF
    # Ab Paperless 3.0 werden gleiche Dateien sonst doppelt angelegt, z. B. eine Rechnung, die schon von Hand in
    # Paperless liegt und beim ersten Abruf noch einmal aus dem Postfach kommt
    if ja "Gleiche Dateien, die schon in Paperless liegen, verwerfen statt doppelt anzulegen?" J; then
        paperless_conf_anpassen PAPERLESS_CONSUMER_DELETE_DUPLICATES=true
    fi
    finde_paperless_url
    # Fehler meldet das Python-Skript selbst
    "$python" -I "$skript" || exit $?
}

# --- Teil 2: Nextcloud-Ordner -> Consume ------------------------------------------------------------------------

webdav_url() {
    local url=${NC_URL%/}
    if [[ $url == */remote.php/* ]]; then
        echo "$url/"
    else
        echo "$url/remote.php/dav/files/$NC_BENUTZER/"
    fi
}

schreibe_rclone() {
    local obscured
    obscured=$(printf '%s' "$NC_APP_PASSWORT" | rclone obscure -)
    umask 077
    cat >"$RCLONE_KONF" <<EOF
# Nextcloud (WebDAV) für paperless-eingang, angelegt $(date +%F). Passwort ist mit "rclone obscure" verschleiert.
[$RCLONE_REMOTE]
type = webdav
url = $(webdav_url)
vendor = nextcloud
user = $NC_BENUTZER
pass = $obscured
EOF
}

schreibe_env() {
    umask 077
    cat >"$ENV_DATEI" <<EOF
# paperless-eingang – Konfiguration (angelegt $(date +%F)). Werte in "…", ohne " \\ \$ und \`.

# lokal = Installation unter PAPERLESS_DIR (z. B. Proxmox-Helper-Script), docker = Container PAPERLESS_CONTAINER
PAPERLESS_ART="$PAPERLESS_ART"
PAPERLESS_DIR="${PAPERLESS_DIR:-}"
PAPERLESS_CONTAINER="${PAPERLESS_CONTAINER:-}"
# Consume-Ordner von Paperless (auf diesem Rechner)
CONSUME_DIR="$CONSUME_DIR"

# Quelle: rclone-Remote (in rclone.conf) und Ordner in der Nextcloud; Dateien werden daraus verschoben
RCLONE_REMOTE="$RCLONE_REMOTE"
NC_ORDNER="$NC_ORDNER"
# Dateien erst holen, wenn sie so lange unverändert liegen (Nextcloud-Client lädt noch hoch)
MIN_ALTER="$MIN_ALTER"
EOF
}

schreibe_units() {
    local nach=network-online.target
    [[ $PAPERLESS_ART == docker ]] && nach="$nach docker.service"
    cat >"$SYSTEMD_DIR/paperless-eingang.service" <<EOF
[Unit]
Description=Dateien aus dem Nextcloud-Eingangsordner an Paperless-ngx übergeben
Wants=network-online.target
After=$nach

[Service]
Type=oneshot
ExecStart=$PROGRAMM abholen
Nice=10
EOF
    cat >"$SYSTEMD_DIR/paperless-eingang.timer" <<EOF
[Unit]
Description=Nextcloud-Eingangsordner alle 2 Minuten prüfen

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now paperless-eingang.timer >/dev/null
}

installiere_programm() {
    mkdir -p "$LIB_DIR"
    local hier=""
    [[ -f ${BASH_SOURCE[0]:-} ]] && hier=$(dirname "$(realpath "${BASH_SOURCE[0]}")")
    if [[ -n $hier && -f $hier/mailabruf.py ]]; then
        [[ $(realpath "${BASH_SOURCE[0]}") == "$PROGRAMM" ]] || install -m 755 "${BASH_SOURCE[0]}" "$PROGRAMM"
        [[ $hier == "$LIB_DIR" ]] || install -m 644 "$hier/mailabruf.py" "$LIB_DIR/mailabruf.py"
    else
        curl -fsSL "$QUELLE_BASIS/paperless-eingang.sh" -o "$PROGRAMM.neu"
        install -m 755 "$PROGRAMM.neu" "$PROGRAMM"
        rm -f "$PROGRAMM.neu"
        curl -fsSL "$QUELLE_BASIS/mailabruf.py" -o "$LIB_DIR/mailabruf.py"
    fi
}

docker_label() {
    docker inspect -f "{{index .Config.Labels \"$1\"}}" "$PAPERLESS_CONTAINER" 2>/dev/null || true
}

docker_umgebung_anpassen() {
    # Wie paperless_conf_anpassen, aber für Docker Compose: Werte in docker-compose.env, Container neu erstellen
    local paar fehlt=() dir dienst datei dateien=() f code i
    for paar in "$@"; do
        [[ $(docker exec "$PAPERLESS_CONTAINER" printenv "${paar%%=*}" 2>/dev/null || true) == "${paar#*=}" ]] \
            || fehlt+=("$paar")
    done
    [[ ${#fehlt[@]} -eq 0 ]] && return 0
    dir=$(docker_label com.docker.compose.project.working_dir)
    dienst=$(docker_label com.docker.compose.service)
    datei=$dir/docker-compose.env
    if [[ -z $dir || -z $dienst || ! -f $datei ]]; then
        warn "Bitte in der Umgebung des Paperless-Containers setzen und ihn neu erstellen${dir:+ (Compose-Verzeichnis $dir)}:"
        warn "  ${fehlt[*]}"
        return 0
    fi
    [[ -f $datei.vor-eingang ]] || cp -a "$datei" "$datei.vor-eingang"
    for paar in "${fehlt[@]}"; do
        setze_zeile "$datei" "${paar%%=*}" "${paar#*=}"
    done
    ok "$datei: ${fehlt[*]} (vorher gesichert als docker-compose.env.vor-eingang)"
    IFS=, read -ra dateien <<<"$(docker_label com.docker.compose.project.config_files)"
    local compose=(docker compose)
    docker compose version >/dev/null 2>&1 || compose=(docker-compose)
    for f in "${dateien[@]}"; do [[ -f $f ]] && compose+=(-f "$f"); done
    info "Erstelle den Container $PAPERLESS_CONTAINER neu, damit die Einstellung greift"
    if ! (cd "$dir" && "${compose[@]}" up -d "$dienst"); then
        warn "Neustart ging nicht; bitte in $dir selbst ausführen: docker compose up -d $dienst"
        return 0
    fi
    # Warten, bis Paperless im Container wieder antwortet (Migrationen und Start dauern etwas)
    for ((i = 0; i < 60; i++)); do
        code=$(docker exec "$PAPERLESS_CONTAINER" curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/ 2>/dev/null || true)
        [[ $code =~ ^(200|301|302|401|403)$ ]] && { ok "Paperless läuft wieder"; return 0; }
        sleep 3
    done
    warn "Paperless antwortet nach 3 Minuten noch nicht; Stand prüfen mit: docker logs $PAPERLESS_CONTAINER"
}

paperless_conf_anpassen() {
    # paperless_conf_anpassen NAME=WERT ...: abweichende Einstellungen setzen und die betroffenen Dienste neu starten
    local paar name wert geaendert=() einheit datei
    if [[ $PAPERLESS_ART == docker ]]; then
        docker_umgebung_anpassen "$@"
        return 0
    fi
    datei=$PAPERLESS_DIR/paperless.conf
    [[ -f $datei ]] || { warn "$datei fehlt; bitte selbst setzen: $*"; return 0; }
    for paar in "$@"; do
        name=${paar%%=*} wert=${paar#*=}
        [[ $(paperless_conf_wert "$name") == "$wert" ]] && continue
        [[ -f $datei.vor-eingang ]] || cp -a "$datei" "$datei.vor-eingang"
        setze_zeile "$datei" "$name" "$wert"
        geaendert+=("$paar")
    done
    [[ ${#geaendert[@]} -eq 0 ]] && return 0
    ok "paperless.conf: ${geaendert[*]} (vorher gesichert als paperless.conf.vor-eingang)"
    # Die Ordnerüberwachung liest die Unterordner-Einstellungen, der Task-Worker verarbeitet Mails und Dokumente
    for einheit in paperless-consumer.service paperless-task-queue.service; do
        if systemctl cat "$einheit" >/dev/null 2>&1; then
            systemctl restart "$einheit"
            ok "$einheit neu gestartet"
        else
            warn "$einheit nicht gefunden; Paperless neu starten, damit die Einstellung greift."
        fi
    done
}

cmd_ordner() {
    [[ ${1:-} == -y ]] && NICHT_FRAGEN=1
    pruefe_root
    command -v systemctl >/dev/null || abbruch "systemd fehlt."
    mkdir -p "$KONF_DIR"
    chmod 700 "$KONF_DIR"
    if [[ -f $ENV_DATEI ]]; then
        info "Vorhandene Konfiguration $ENV_DATEI wird als Vorgabe genommen"
        set -a
        # shellcheck source=/dev/null
        . "$ENV_DATEI"
        set +a
    fi
    installiere_pakete rclone curl flock
    erkenne_paperless
    ok "Paperless gefunden: $PAPERLESS_ART ${PAPERLESS_CONTAINER:-${PAPERLESS_DIR:-}}, Consume-Ordner $CONSUME_DIR"

    RCLONE_REMOTE=${RCLONE_REMOTE:-nextcloud}
    NC_ORDNER=${NC_ORDNER:-__unverarbeitet__}
    MIN_ALTER=${MIN_ALTER:-1m}

    local rclone_neu=1
    if [[ -f $RCLONE_KONF ]] && rclone listremotes --config "$RCLONE_KONF" 2>/dev/null | grep -qx "$RCLONE_REMOTE:"; then
        rclone_neu=0
        ja "Nextcloud-Zugang aus $RCLONE_KONF behalten?" J || rclone_neu=1
    elif [[ -f $BACKUP_RCLONE_KONF ]] && rclone listremotes --config "$BACKUP_RCLONE_KONF" 2>/dev/null | grep -qx "$RCLONE_REMOTE:"; then
        if ja "Nextcloud-Zugang von paperless-backup übernehmen?" J; then
            install -m 600 "$BACKUP_RCLONE_KONF" "$RCLONE_KONF"
            rclone_neu=0
        fi
    fi
    if [[ $rclone_neu == 1 ]]; then
        NC_URL=${NC_URL:-} NC_BENUTZER=${NC_BENUTZER:-} NC_APP_PASSWORT=${NC_APP_PASSWORT:-}
        frage NC_URL "Nextcloud-Adresse (z. B. https://nx12345.your-storageshare.de)"
        frage NC_BENUTZER "Nextcloud-Benutzername"
        frage NC_APP_PASSWORT "Nextcloud-App-Passwort (Einstellungen > Sicherheit > Neues App-Passwort)" geheim
        [[ $NC_URL == http*://* && -n $NC_BENUTZER && -n $NC_APP_PASSWORT ]] \
            || abbruch "Nextcloud-Adresse (https://…), Benutzer und App-Passwort werden gebraucht."
        schreibe_rclone
    fi
    export RCLONE_CONFIG=$RCLONE_KONF
    info "Teste Verbindung zur Nextcloud"
    rclone lsd "$RCLONE_REMOTE:" >/dev/null \
        || abbruch "Nextcloud nicht erreichbar oder Anmeldung falsch (Adresse, Benutzer, App-Passwort prüfen)."
    ok "Nextcloud erreichbar"

    while :; do
        frage NC_ORDNER "Eingangsordner in der Nextcloud (Pfad wie in der Nextcloud, z. B. Finanzen/__unverarbeitet__)"
        NC_ORDNER=${NC_ORDNER#/}
        NC_ORDNER=${NC_ORDNER%/}
        rclone lsf --max-depth 1 "$RCLONE_REMOTE:$NC_ORDNER" >/dev/null 2>&1 && break
        [[ $NICHT_FRAGEN == 1 ]] && abbruch "Ordner $NC_ORDNER gibt es in der Nextcloud nicht."
        warn "Ordner $NC_ORDNER gibt es in der Nextcloud nicht. Ordner in der Nextcloud oben: $(rclone lsf --dirs-only "$RCLONE_REMOTE:" | tr '\n' ' ')"
    done
    local var
    for var in PAPERLESS_DIR PAPERLESS_CONTAINER CONSUME_DIR RCLONE_REMOTE NC_ORDNER MIN_ALTER; do
        pruefe_wert "$var" "${!var:-}"
    done

    if ja "Unterordner als Tags nutzen (z. B. $NC_ORDNER/Kontoauszug/… bekommt den Tag Kontoauszug)?" J; then
        paperless_conf_anpassen PAPERLESS_CONSUMER_RECURSIVE=true PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS=true
        local u
        for u in $UNTERORDNER_VORSCHLAG; do
            rclone mkdir "$RCLONE_REMOTE:$NC_ORDNER/$u"
        done
        ok "Unterordner in der Nextcloud: $UNTERORDNER_VORSCHLAG (weitere einfach selbst anlegen)"
    fi

    schreibe_env
    installiere_programm
    mkdir -p "$ZWISCHEN_DIR"
    local wartend
    wartend=$(rclone lsf -R --files-only "$RCLONE_REMOTE:$NC_ORDNER" 2>/dev/null | grep -vc '^\.\|/\.' || true)
    if [[ $wartend -gt 0 ]]; then
        warn "Im Ordner liegen schon $wartend Datei(en). Der Timer übergibt sie alle an Paperless und entfernt sie aus der Nextcloud."
        ja "Trotzdem einschalten?" J || { ok "Timer nicht eingeschaltet. Später: paperless-eingang ordner"; return 0; }
    fi
    schreibe_units
    ok "Timer aktiv: prüft $RCLONE_REMOTE:$NC_ORDNER alle 2 Minuten. Verfolgen mit: journalctl -fu paperless-eingang"
}

# --- Abholen (Timer) --------------------------------------------------------------------------------------------

uebergeben() {
    # Datei aus dem Zwischenordner atomar in den Consume-Ordner legen: erst unter einem Namen kopieren, den
    # Paperless ignoriert, dann umbenennen. Gleichnamige Dateien bekommen einen Zeitstempel.
    local datei=$1 relativ ziel_dir name ziel tmp
    relativ=${datei#"$ZWISCHEN_DIR"/}
    ziel_dir=$CONSUME_DIR/$(dirname "$relativ")
    ziel_dir=${ziel_dir%/.}
    name=$(basename "$relativ")
    mkdir -p "$ziel_dir"
    chown --reference="$CONSUME_DIR" "$ziel_dir"
    ziel=$ziel_dir/$name
    [[ -e $ziel ]] && ziel=$ziel_dir/$(date +%Y%m%d-%H%M%S)-$name
    tmp=$ziel_dir/.$name.eingang-tmp
    cp "$datei" "$tmp"
    chown --reference="$CONSUME_DIR" "$tmp"
    chmod 644 "$tmp"
    mv -n "$tmp" "$ziel"
    rm -f "$datei"
    echo "übergeben: $relativ"
}

zwischenordner_leeren() {
    local datei anzahl=0
    while IFS= read -r -d '' datei; do
        uebergeben "$datei"
        anzahl=$((anzahl + 1))
    done < <(find "$ZWISCHEN_DIR" -type f -print0)
    find "$ZWISCHEN_DIR" -mindepth 1 -type d -empty -delete
    [[ $anzahl -gt 0 ]] && echo "$anzahl Datei(en) an Paperless übergeben"
    return 0
}

cmd_abholen() {
    pruefe_root
    lade_konfig
    exec 9>"$SPERRE"
    flock -n 9 || exit 0
    [[ -d $CONSUME_DIR ]] || abbruch "Consume-Ordner $CONSUME_DIR fehlt."
    mkdir -p "$ZWISCHEN_DIR"
    # Reste eines abgebrochenen Laufs zuerst
    zwischenordner_leeren
    # Versteckte Dateien, Office-Sperrdateien und halbe Uploads bleiben liegen; leere Ordner bleiben stehen
    rclone move "$RCLONE_REMOTE:$NC_ORDNER" "$ZWISCHEN_DIR" \
        --min-age "${MIN_ALTER:-1m}" \
        --exclude '.*' --exclude '.*/**' --exclude '~$*' --exclude '*.part' --exclude '*.tmp' \
        --exclude 'Thumbs.db' --exclude 'desktop.ini' \
        --log-level NOTICE
    zwischenordner_leeren
}

cmd_status() {
    pruefe_root
    lade_konfig
    systemctl list-timers paperless-eingang.timer --no-pager 2>/dev/null || true
    echo
    echo "Wartend in $RCLONE_REMOTE:$NC_ORDNER:"
    rclone lsf -R --files-only "$RCLONE_REMOTE:$NC_ORDNER" | sed 's/^/  /' || true
    echo
    echo "Letzte Übergaben:"
    journalctl -u paperless-eingang --no-pager -n 20 -o cat 2>/dev/null | grep -E 'übergeben|Fehler|ERROR' | tail -n 10 || true
}

cmd_einrichten() {
    if ja "Mail-Abruf einrichten (Paperless holt Rechnungen aus dem Postfach)?" J; then
        cmd_mail
        echo
    fi
    if ja "Nextcloud-Eingangsordner einrichten (Dateien wandern automatisch nach Paperless)?" J; then
        cmd_ordner
    fi
    [[ -x $PROGRAMM ]] || installiere_programm
    ok "Fertig. Später ändern mit: paperless-eingang einrichten | mail | ordner"
}

main() {
    local befehl=${1:-hilfe}
    shift || true
    case $befehl in
        einrichten) cmd_einrichten ;;
        mail) cmd_mail ;;
        ordner) cmd_ordner "$@" ;;
        abholen) cmd_abholen ;;
        status) cmd_status ;;
        rclone) lade_konfig && exec rclone "$@" ;;
        -h | --help | hilfe) hilfe ;;
        *)
            hilfe >&2
            exit 2
            ;;
    esac
}

main "$@"
