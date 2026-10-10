#!/usr/bin/env bash
# Paperless-ngx sichern: document_exporter -> restic (verschlüsselt) -> rclone -> Nextcloud (WebDAV).
# Läuft als root dort, wo Paperless läuft (Helper-Script-LXC oder Docker-Host). Aufruf siehe --help und README.md.

set -Eeuo pipefail

QUELLE_URL="${QUELLE_URL:-https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/paperless-backup/paperless-backup.sh}"
KONF_DIR="${PAPERLESS_BACKUP_KONF:-/etc/paperless-backup}"
ENV_DATEI=$KONF_DIR/paperless-backup.env
RCLONE_KONF=$KONF_DIR/rclone.conf
PASSWORT_DATEI=$KONF_DIR/restic-passwort
PROGRAMM=/usr/local/sbin/paperless-backup
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SPERRE=/run/paperless-backup.lock
# Exportordner im Docker-Container; das Skript nutzt darin einen eigenen Unterordner, weil --delete aufräumt
DOCKER_EXPORT=/usr/src/paperless/export
UNTERORDNER=paperless-backup

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
Paperless-ngx regelmäßig nach Nextcloud sichern (als root, dort wo Paperless läuft).

Aufruf: paperless-backup <befehl>

  einrichten [-y]               restic/rclone installieren, Nextcloud verbinden, Timer anlegen (erneut = ändern/aktualisieren)
  sichern                       Export + Sicherung + Aufräumen (macht der Timer täglich)
  status                        Timer, letzter Lauf, letzte Sicherungen
  liste                         alle Sicherungen (Snapshots)
  pruefen [ANTEIL]              Sicherung prüfen, z. B. "pruefen 100%" lädt alles einmal herunter
  wiederherstellen [ID] [ZIEL]  Sicherung (Vorgabe: latest) nach ZIEL (Vorgabe: /var/tmp/paperless-wiederherstellung) holen
  restic ... | rclone ...       restic bzw. rclone mit der hinterlegten Konfiguration aufrufen

Erstinstallation:
  bash -c "\$(curl -fsSL $QUELLE_URL)" -- einrichten
Details: deploy/paperless-backup/README.md im Repo finanz-O-mat
EOF
}

pruefe_root() { [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."; }

# Werte landen in einer Datei, die systemd und die Shell gleichermaßen lesen; Sonderzeichen würden das brechen.
pruefe_wert() {
    local name=$1 wert=$2
    [[ $wert != *[\"\\\$\`]* && $wert != *$'\n'* ]] \
        || abbruch "$name darf keine Anführungszeichen, Backslashes, \$ oder \` enthalten"
}

zufall() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-32}" || true; }

# frage VAR "Text" [geheim]: fragt einen Wert ab, Vorgabe ist der aktuelle Inhalt von VAR
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
    # ja "Frage" [J|N]: Vorgabe J; ohne Rückfragen gilt die Vorgabe
    local antwort vorgabe=${2:-J}
    [[ $NICHT_FRAGEN == 1 ]] && { [[ $vorgabe == J ]]; return; }
    read -rp "$1 [$([[ $vorgabe == J ]] && echo J/n || echo j/N)]: " antwort
    antwort=${antwort:-$vorgabe}
    [[ $antwort == [JjYy]* ]]
}

lade_konfig() {
    [[ -f $ENV_DATEI ]] || abbruch "$ENV_DATEI fehlt. Erst einrichten: paperless-backup einrichten"
    set -a
    # shellcheck source=/dev/null
    . "$ENV_DATEI"
    set +a
    export RCLONE_CONFIG=$RCLONE_KONF
    export RESTIC_PASSWORD_FILE=$PASSWORT_DATEI
    export RESTIC_REPOSITORY="rclone:${RCLONE_REMOTE:-nextcloud}:${ZIEL_ORDNER:-Backups/paperless}"
    export RESTIC_CACHE_DIR=${RESTIC_CACHE_DIR:-/var/cache/paperless-backup}
}

# --- Paperless finden und exportieren ---------------------------------------------------------------------------

docker_container() {
    command -v docker >/dev/null || return 0
    docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | awk '$2 ~ /paperless-ngx/ {print $1; exit}'
}

docker_export_mount() {
    docker inspect -f "{{range .Mounts}}{{if eq .Destination \"$DOCKER_EXPORT\"}}{{.Source}}{{end}}{{end}}" "$1"
}

erkenne_paperless() {
    # Setzt PAPERLESS_ART (lokal|docker), PAPERLESS_DIR bzw. PAPERLESS_CONTAINER und EXPORT_DIR, soweit noch leer.
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
            [[ -f $PAPERLESS_DIR/src/manage.py ]] || abbruch "$PAPERLESS_DIR/src/manage.py fehlt (PAPERLESS_DIR prüfen)."
            EXPORT_DIR=${EXPORT_DIR:-/var/backups/paperless-export}
            ;;
        docker)
            [[ -n ${PAPERLESS_CONTAINER:-} ]] || PAPERLESS_CONTAINER=$(docker_container)
            [[ -n $PAPERLESS_CONTAINER ]] || abbruch "Kein laufender paperless-ngx-Container gefunden (PAPERLESS_CONTAINER setzen)."
            if [[ -z ${EXPORT_DIR:-} ]]; then
                local mount
                mount=$(docker_export_mount "$PAPERLESS_CONTAINER")
                [[ -n $mount ]] || abbruch "Container $PAPERLESS_CONTAINER hat keinen Ordner unter $DOCKER_EXPORT eingebunden (in docker-compose.yml: - ./export:$DOCKER_EXPORT)."
                EXPORT_DIR=$mount/$UNTERORDNER
            fi
            ;;
        *) abbruch "PAPERLESS_ART muss lokal, docker oder auto sein, nicht '$PAPERLESS_ART'." ;;
    esac
}

zusatz_vorschlag() {
    # Konfiguration, die der Export nicht enthält (Secret Key, DB-Zugang, Compose-Datei), mitsichern
    local dir
    if [[ $PAPERLESS_ART == lokal ]]; then
        [[ -f $PAPERLESS_DIR/paperless.conf ]] && echo "$PAPERLESS_DIR/paperless.conf"
    else
        dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$PAPERLESS_CONTAINER" 2>/dev/null || true)
        [[ -n $dir && -d $dir ]] || return 0
        find "$dir" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' -o -name '*.env' -o -name '.env' \) | sort
    fi
    return 0
}

exportieren() {
    # shellcheck disable=SC2206 # EXPORT_OPTIONEN sind bewusst mehrere Wörter
    local optionen=(--delete --no-progress-bar ${EXPORT_OPTIONEN:-})
    if [[ $PAPERLESS_ART == docker ]]; then
        info "Exportiere Paperless aus Container $PAPERLESS_CONTAINER nach $EXPORT_DIR"
        mkdir -p "$EXPORT_DIR"
        # Der Container schreibt als Paperless-Benutzer (USERMAP_UID), der Ordner muss ihm gehören
        chown "$(stat -c %u:%g "$(dirname "$EXPORT_DIR")")" "$EXPORT_DIR"
        docker exec "$PAPERLESS_CONTAINER" document_exporter "$DOCKER_EXPORT/$UNTERORDNER" "${optionen[@]}"
    else
        local python=$PAPERLESS_DIR/.venv/bin/python benutzer
        [[ -x $python ]] || python=$(command -v python3)
        # Helper-Script-Installationen laufen als root, manuelle meist als Benutzer paperless
        benutzer=${PAPERLESS_BENUTZER:-$(stat -c %U "$PAPERLESS_DIR/src")}
        info "Exportiere Paperless aus $PAPERLESS_DIR nach $EXPORT_DIR (als $benutzer)"
        mkdir -p "$EXPORT_DIR"
        chown "$benutzer:" "$EXPORT_DIR"
        chmod 700 "$EXPORT_DIR"
        # Paperless findet ../paperless.conf relativ zu src/
        cd "$PAPERLESS_DIR/src"
        if [[ $benutzer == root ]]; then
            "$python" manage.py document_exporter "$EXPORT_DIR" "${optionen[@]}"
        else
            runuser -u "$benutzer" -- "$python" manage.py document_exporter "$EXPORT_DIR" "${optionen[@]}"
        fi
    fi
    [[ -f $EXPORT_DIR/manifest.json ]] || abbruch "Export unvollständig: $EXPORT_DIR/manifest.json fehlt."
    ok "Export fertig: $(du -sh "$EXPORT_DIR" | cut -f1), $(find "$EXPORT_DIR" -type f | wc -l) Dateien"
}

# --- Benachrichtigung -------------------------------------------------------------------------------------------

melde() {
    # melde start|ok|fehler [Text]: Ping-URL (healthchecks.io oder Push-Monitor von Uptime Kuma) und/oder ntfy
    local art=$1 text=${2:-} url=${HC_PING_URL:-}
    # shellcheck disable=SC2001 # Farbcodes per Regex entfernen
    text=$(sed 's/\x1b\[[0-9;]*m//g' <<<"$text")
    if [[ $url == */api/push/* ]]; then
        # Uptime Kuma kennt nur up/down, der Text geht als msg mit
        if [[ $art != start ]]; then
            url="${url%%\?*}?status=$([[ $art == fehler ]] && echo down || echo up)"
            curl -fsS -m 15 --retry 3 -o /dev/null -G --data-urlencode "msg=$(tail -n 1 <<<"$text" | cut -c1-200)" \
                "$url" || warn "Ping an HC_PING_URL fehlgeschlagen"
        fi
    elif [[ -n $url ]]; then
        case $art in start) url=${url%/}/start ;; fehler) url=${url%/}/fail ;; *) url=${url%/} ;; esac
        curl -fsS -m 15 --retry 3 -o /dev/null --data-raw "$text" "$url" || warn "Ping an HC_PING_URL fehlgeschlagen"
    fi
    if [[ -n ${NTFY_URL:-} ]] && { [[ $art == fehler ]] || { [[ $art == ok && ${NTFY_BEI_ERFOLG:-nein} == ja ]]; }; }; then
        local kopf=(-H "Title: Paperless-Backup $([[ $art == ok ]] && echo erfolgreich || echo fehlgeschlagen) ($(hostname))")
        [[ $art == fehler ]] && kopf+=(-H "Priority: high" -H "Tags: warning")
        [[ -n ${NTFY_TOKEN:-} ]] && kopf+=(-H "Authorization: Bearer $NTFY_TOKEN")
        curl -fsS -m 15 --retry 3 -o /dev/null "${kopf[@]}" --data-raw "${text:0:3500}" "$NTFY_URL" \
            || warn "Nachricht an NTFY_URL fehlgeschlagen"
    fi
    return 0
}

# --- Sichern ----------------------------------------------------------------------------------------------------

sicherung() {
    local pfade=() pfad
    erkenne_paperless
    exportieren
    pfade+=("$EXPORT_DIR")
    for pfad in ${ZUSATZ_PFADE:-}; do
        if [[ -e $pfad ]]; then pfade+=("$pfad"); else warn "Zusatzpfad $pfad fehlt, übersprungen"; fi
    done

    info "Sichere nach $RESTIC_REPOSITORY"
    restic backup --host "${SICHERUNG_HOST:-$(hostname)}" --tag paperless "${pfade[@]}"

    info "Räume alte Sicherungen auf (${BEHALTEN})"
    # shellcheck disable=SC2086 # BEHALTEN sind mehrere Optionen
    restic forget --host "${SICHERUNG_HOST:-$(hostname)}" --tag paperless --prune ${BEHALTEN}

    if [[ -n ${PRUEF_ANTEIL:-} ]]; then
        info "Prüfe Sicherung (Stichprobe ${PRUEF_ANTEIL} der Daten)"
        restic check --read-data-subset "$PRUEF_ANTEIL"
    else
        info "Prüfe Sicherung (Struktur)"
        restic check
    fi
    restic snapshots --host "${SICHERUNG_HOST:-$(hostname)}" --tag paperless --latest 1 --compact
}

cmd_sichern() {
    pruefe_root
    lade_konfig
    exec 9>"$SPERRE"
    flock -n 9 || abbruch "Es läuft schon eine Sicherung."

    local rc
    LOG_DATEI=$(mktemp)
    trap 'rm -f "$LOG_DATEI"' EXIT
    melde start
    # Fehler im Lauf melden statt abzubrechen: Subshell mit eigenem set -e, außen weder ERR-Trap noch -e
    trap - ERR
    set +e
    (
        set -e
        trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR
        sicherung
    ) 2>&1 | tee "$LOG_DATEI"
    rc=${PIPESTATUS[0]}
    set -e
    if [[ $rc -eq 0 ]]; then
        ok "Sicherung erfolgreich"
        melde ok "$(tail -n 20 "$LOG_DATEI")"
    else
        warn "Sicherung fehlgeschlagen (Exit $rc)"
        melde fehler "$(tail -n 40 "$LOG_DATEI")"
        exit "$rc"
    fi
}

# --- Einrichten -------------------------------------------------------------------------------------------------

installiere_pakete() {
    local fehlt=()
    command -v restic >/dev/null || fehlt+=(restic)
    command -v rclone >/dev/null || fehlt+=(rclone)
    command -v curl >/dev/null || fehlt+=(curl)
    command -v flock >/dev/null || fehlt+=(util-linux)
    [[ ${#fehlt[@]} -eq 0 ]] && return 0
    command -v apt-get >/dev/null || abbruch "Bitte ${fehlt[*]} installieren (kein apt-get gefunden)."
    info "Installiere ${fehlt[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${fehlt[@]}" >/dev/null
}

webdav_url() {
    # Akzeptiert die Nextcloud-Adresse oder gleich die WebDAV-Adresse aus „Dateien-Einstellungen“
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
# Nextcloud (WebDAV) für paperless-backup, angelegt $(date +%F). Passwort ist mit "rclone obscure" verschleiert.
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
# paperless-backup – Konfiguration (angelegt $(date +%F)). Werte in "…", ohne " \\ \$ und \`.
# Nach Änderungen an SICHERUNG_ZEIT: paperless-backup einrichten erneut ausführen.

# lokal = Installation unter PAPERLESS_DIR (z. B. Proxmox-Helper-Script), docker = Container PAPERLESS_CONTAINER
PAPERLESS_ART="$PAPERLESS_ART"
PAPERLESS_DIR="${PAPERLESS_DIR:-}"
PAPERLESS_CONTAINER="${PAPERLESS_CONTAINER:-}"
# Ordner für den Export (wird bei jedem Lauf abgeglichen, nicht neu geschrieben); braucht etwa so viel Platz wie media/
EXPORT_DIR="$EXPORT_DIR"
# weitere Optionen für document_exporter, z. B. "--no-thumbnail"
EXPORT_OPTIONEN="${EXPORT_OPTIONEN:-}"
# zusätzlich gesicherte Dateien (mit Leerzeichen getrennt)
ZUSATZ_PFADE="$ZUSATZ_PFADE"

# Ziel: rclone-Remote (in rclone.conf) und Ordner in der Nextcloud
RCLONE_REMOTE="$RCLONE_REMOTE"
ZIEL_ORDNER="$ZIEL_ORDNER"
SICHERUNG_HOST="$SICHERUNG_HOST"

# Uhrzeit des täglichen Laufs (HH:MM), Aufbewahrung, Stichprobe der Prüfung (leer = nur Struktur)
SICHERUNG_ZEIT="$SICHERUNG_ZEIT"
BEHALTEN="$BEHALTEN"
PRUEF_ANTEIL="$PRUEF_ANTEIL"

# Benachrichtigung: Ping-URL im Stil von healthchecks.io (meldet auch, wenn gar nichts läuft) und/oder ntfy-Thema
HC_PING_URL="${HC_PING_URL:-}"
NTFY_URL="${NTFY_URL:-}"
NTFY_TOKEN="${NTFY_TOKEN:-}"
NTFY_BEI_ERFOLG="${NTFY_BEI_ERFOLG:-nein}"
EOF
}

schreibe_units() {
    local nach=network-online.target
    [[ $PAPERLESS_ART == docker ]] && nach="$nach docker.service"
    cat >"$SYSTEMD_DIR/paperless-backup.service" <<EOF
[Unit]
Description=Paperless-ngx nach Nextcloud sichern
Wants=network-online.target
After=$nach

[Service]
Type=oneshot
ExecStart=$PROGRAMM sichern
Nice=10
IOSchedulingClass=idle
EOF
    cat >"$SYSTEMD_DIR/paperless-backup.timer" <<EOF
[Unit]
Description=Paperless-ngx täglich sichern

[Timer]
OnCalendar=*-*-* $SICHERUNG_ZEIT:00
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now paperless-backup.timer >/dev/null
}

installiere_programm() {
    if [[ -f ${BASH_SOURCE[0]:-} ]]; then
        [[ $(realpath "${BASH_SOURCE[0]}") == "$PROGRAMM" ]] || install -m 755 "${BASH_SOURCE[0]}" "$PROGRAMM"
    else
        curl -fsSL "$QUELLE_URL" -o "$PROGRAMM.neu"
        install -m 755 "$PROGRAMM.neu" "$PROGRAMM"
        rm -f "$PROGRAMM.neu"
    fi
}

repo_vorbereiten() {
    # Neues Repository anlegen oder ein vorhandenes mit dem richtigen Passwort weiterverwenden
    if [[ -n $(rclone lsf "$RCLONE_REMOTE:$ZIEL_ORDNER/config" 2>/dev/null) ]]; then
        if [[ ! -s $PASSWORT_DATEI ]]; then
            [[ $NICHT_FRAGEN == 1 && -z ${RESTIC_PASSWORT:-} ]] \
                && abbruch "In $ZIEL_ORDNER liegt schon eine Sicherung, RESTIC_PASSWORT fehlt."
            frage RESTIC_PASSWORT "In $ZIEL_ORDNER liegt schon eine Sicherung. Deren restic-Passwort" geheim
            printf '%s\n' "$RESTIC_PASSWORT" >"$PASSWORT_DATEI"
            if ! restic cat config >/dev/null; then
                rm -f "$PASSWORT_DATEI"
                abbruch "Das Passwort passt nicht zur vorhandenen Sicherung in $ZIEL_ORDNER."
            fi
        fi
        restic cat config >/dev/null || abbruch "Vorhandene Sicherung lässt sich mit dem Passwort aus $PASSWORT_DATEI nicht öffnen."
        ok "Vorhandene Sicherung in $ZIEL_ORDNER wird weiterverwendet"
    else
        if [[ ! -s $PASSWORT_DATEI ]]; then
            printf '%s\n' "${RESTIC_PASSWORT:-$(zufall 32)}" >"$PASSWORT_DATEI"
            NEUES_PASSWORT=1
        fi
        rclone mkdir "$RCLONE_REMOTE:$ZIEL_ORDNER"
        restic init >/dev/null
        ok "Neue verschlüsselte Sicherung in $ZIEL_ORDNER angelegt"
    fi
}

cmd_einrichten() {
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

    installiere_pakete
    erkenne_paperless
    ok "Paperless gefunden: $PAPERLESS_ART ${PAPERLESS_CONTAINER:-${PAPERLESS_DIR:-}}"
    [[ -n ${ZUSATZ_PFADE+x} ]] || ZUSATZ_PFADE=$(zusatz_vorschlag | tr '\n' ' ' | sed 's/ $//')

    RCLONE_REMOTE=${RCLONE_REMOTE:-nextcloud}
    ZIEL_ORDNER=${ZIEL_ORDNER:-Backups/paperless}
    SICHERUNG_HOST=${SICHERUNG_HOST:-$(hostname)}
    SICHERUNG_ZEIT=${SICHERUNG_ZEIT:-02:30}
    BEHALTEN=${BEHALTEN:---keep-daily 7 --keep-weekly 5 --keep-monthly 12 --keep-yearly 10}
    PRUEF_ANTEIL=${PRUEF_ANTEIL-2%}

    local rclone_neu=1
    if [[ -f $RCLONE_KONF ]] && rclone listremotes --config "$RCLONE_KONF" 2>/dev/null | grep -qx "$RCLONE_REMOTE:"; then
        rclone_neu=0
        ja "Nextcloud-Zugang aus $RCLONE_KONF behalten?" J || rclone_neu=1
    fi
    if [[ $rclone_neu == 1 ]]; then
        NC_URL=${NC_URL:-} NC_BENUTZER=${NC_BENUTZER:-} NC_APP_PASSWORT=${NC_APP_PASSWORT:-}
        frage NC_URL "Nextcloud-Adresse (z. B. https://nx12345.your-storageshare.de)"
        frage NC_BENUTZER "Nextcloud-Benutzername"
        frage NC_APP_PASSWORT "Nextcloud-App-Passwort (Einstellungen > Sicherheit > Neues App-Passwort)" geheim
        [[ $NC_URL == http*://* && -n $NC_BENUTZER && -n $NC_APP_PASSWORT ]] \
            || abbruch "Nextcloud-Adresse (https://…), Benutzer und App-Passwort werden gebraucht."
    fi
    frage ZIEL_ORDNER "Ordner in der Nextcloud"
    frage SICHERUNG_ZEIT "Uhrzeit der täglichen Sicherung (HH:MM)"
    frage BEHALTEN "Aufbewahrung"
    frage HC_PING_URL "Ping-URL für Überwachung, z. B. https://hc-ping.com/… (leer = keine)"
    frage NTFY_URL "ntfy-Thema für Fehlermeldungen, z. B. https://ntfy.sh/mein-thema (leer = keins)"
    [[ $SICHERUNG_ZEIT =~ ^[0-2][0-9]:[0-5][0-9]$ ]] || abbruch "Uhrzeit bitte als HH:MM."
    local var
    for var in PAPERLESS_DIR PAPERLESS_CONTAINER EXPORT_DIR EXPORT_OPTIONEN ZUSATZ_PFADE ZIEL_ORDNER SICHERUNG_HOST \
        BEHALTEN PRUEF_ANTEIL HC_PING_URL NTFY_URL NTFY_TOKEN; do
        pruefe_wert "$var" "${!var:-}"
    done

    [[ $rclone_neu == 1 ]] && schreibe_rclone
    export RCLONE_CONFIG=$RCLONE_KONF
    info "Teste Verbindung zur Nextcloud"
    rclone lsd "$RCLONE_REMOTE:" >/dev/null \
        || abbruch "Nextcloud nicht erreichbar oder Anmeldung falsch (Adresse, Benutzer, App-Passwort prüfen)."
    ok "Nextcloud erreichbar"

    schreibe_env
    lade_konfig
    NEUES_PASSWORT=0
    repo_vorbereiten
    mkdir -p "$RESTIC_CACHE_DIR"
    installiere_programm
    schreibe_units
    ok "Timer aktiv: täglich gegen $SICHERUNG_ZEIT ($(systemctl show paperless-backup.timer -p NextElapseUSecRealtime --value 2>/dev/null))"

    if [[ $NEUES_PASSWORT == 1 ]]; then
        echo
        printf '\e[1;33m%s\e[0m\n' "WICHTIG: Ohne dieses Passwort ist die Sicherung wertlos. Jetzt in den Passwortmanager übernehmen:"
        echo "  restic-Passwort:  $(cat "$PASSWORT_DATEI")"
        echo "  Repository:       $RESTIC_REPOSITORY  ($(awk '$1 == "url" {print $3}' "$RCLONE_KONF"))"
        echo "(liegt auch in $PASSWORT_DATEI, aber das hilft nichts, wenn dieser Rechner weg ist)"
        echo
    fi
    if ja "Erste Sicherung jetzt im Hintergrund starten?" J; then
        systemctl start --no-block paperless-backup.service
        ok "Läuft. Verfolgen mit: journalctl -fu paperless-backup"
    fi
}

# --- Anzeigen, prüfen, wiederherstellen ---------------------------------------------------------------------------

cmd_status() {
    pruefe_root
    lade_konfig
    systemctl list-timers paperless-backup.timer --no-pager 2>/dev/null || true
    echo
    systemctl show paperless-backup.service -p Result -p ExecMainExitTimestamp --no-pager 2>/dev/null || true
    echo
    restic snapshots --tag paperless --latest 3 --compact
}

cmd_wiederherstellen() {
    pruefe_root
    lade_konfig
    local snapshot=${1:-latest} ziel=${2:-/var/tmp/paperless-wiederherstellung} manifest exportdir version
    [[ -e $ziel && -n $(ls -A "$ziel" 2>/dev/null) ]] && abbruch "$ziel ist nicht leer."
    info "Hole Sicherung $snapshot nach $ziel"
    restic restore "$snapshot" --target "$ziel"
    manifest=$(find "$ziel" -name manifest.json | head -n1)
    [[ -n $manifest ]] || abbruch "Kein manifest.json in der Sicherung gefunden."
    exportdir=$(dirname "$manifest")
    version=$(grep -o '"version": *"[^"]*"' "$exportdir/metadata.json" 2>/dev/null | cut -d'"' -f4 || true)
    ok "Export liegt in $exportdir${version:+ (Paperless $version)}"
    cat <<EOF

Weiter (siehe README, Abschnitt "Wiederherstellen"):
  1. Paperless ${version:-in derselben Version} frisch installieren (leere Datenbank) und einmal starten.
  2. Den Export importieren:
     Helper-Script/lokal: cd /opt/paperless/src && ../.venv/bin/python manage.py document_importer $exportdir
                          (ohne .venv: python3 manage.py document_importer …)
     Docker:              cp -a $exportdir/. <export-Ordner>/import/ && docker compose exec -T webserver document_importer ../export/import
  3. paperless.conf bzw. docker-compose*.yml/.env aus $ziel bei Bedarf zurückkopieren (PAPERLESS_SECRET_KEY usw.).
EOF
}

main() {
    local befehl=${1:-hilfe}
    shift || true
    case $befehl in
        einrichten) cmd_einrichten "$@" ;;
        sichern) cmd_sichern ;;
        status) cmd_status ;;
        liste) lade_konfig && restic snapshots ;;
        pruefen) lade_konfig && restic check --read-data-subset "${1:-5%}" ;;
        wiederherstellen) cmd_wiederherstellen "$@" ;;
        restic) lade_konfig && exec restic "$@" ;;
        rclone) lade_konfig && exec rclone "$@" ;;
        -h | --help | hilfe) hilfe ;;
        *)
            hilfe >&2
            exit 2
            ;;
    esac
}

main "$@"
