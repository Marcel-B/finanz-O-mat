#!/usr/bin/env bash
# Backup von Datenbank und Konfiguration auf eine Nextcloud (WebDAV), verschlüsselt mit restic.
# Läuft im Container, aufrufbar als finanz-o-mat-backup:
#
#   finanz-o-mat-backup einrichten       Nextcloud-Zugang und Backup-Passwort festlegen, erstes Backup
#   finanz-o-mat-backup [jetzt]          Backup erstellen, alte Stände aufräumen (läuft täglich per Timer)
#   finanz-o-mat-backup liste            vorhandene Backups anzeigen
#   finanz-o-mat-backup wiederherstellen [ID] [--mit-konfig] [-y]
#                                        Datenbank aus einem Backup zurückspielen (ohne ID: neuestes)
#
# Zugangsdaten liegen nur für root lesbar unter /etc/finanz-o-mat/ (backup.env, backup.passwort, rclone.conf).
# Ohne das Backup-Passwort lassen sich die Backups nicht entschlüsseln: es gehört zusätzlich in den Passwortmanager.
# Ohne Rückfragen einrichten: BACKUP_URL, BACKUP_BENUTZER, BACKUP_APP_PASSWORT, optional BACKUP_ORDNER und
# BACKUP_PASSWORT (für ein vorhandenes Backup) als Umgebungsvariablen setzen.

set -Eeuo pipefail

KONFIG=/etc/finanz-o-mat
ENV_DATEI=$KONFIG/finanz-o-mat.env
BACKUP_ENV=$KONFIG/backup.env
PASSWORT_DATEI=$KONFIG/backup.passwort
RCLONE_CONF=$KONFIG/rclone.conf
DATEN=/var/lib/finanz-o-mat
STATUS_DIR=$DATEN/backup
# Was ins Backup geht, wird hier zusammengestellt (fester Pfad, damit er in allen Ständen gleich ist)
INHALT=/var/backups/finanz-o-mat
BENUTZER=finanzomat
DIENST=finanz-o-mat
HOST=finanz-o-mat
SPERRE=/run/finanz-o-mat-backup.lock
BEHALTEN=(--keep-daily 30 --keep-monthly 12 --keep-yearly 10)

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    if [[ -n ${BACKUP_LAEUFT:-} ]]; then
        unset BACKUP_LAEUFT
        status_schreiben fehler "$*" || true
    fi
    exit 1
}
trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR

hat_systemd() { [[ -d /run/systemd/system ]]; }
root_pruefen() { [[ $EUID -eq 0 ]] || abbruch "Bitte als root ausführen."; }

env_wert() {
    sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}\$/\1/p" "$ENV_DATEI" 2>/dev/null | tail -n1
}

datenbank() {
    local db
    db=$(env_wert DATENBANK)
    echo "${db:-$DATEN/finanzomat.sqlite}"
}

repo_umgebung() {
    [[ -f $BACKUP_ENV ]] || abbruch "Backup ist nicht eingerichtet: finanz-o-mat-backup einrichten"
    # shellcheck source=/dev/null
    . "$BACKUP_ENV"
    export RESTIC_REPOSITORY="rclone:nextcloud:$BACKUP_ORDNER"
    export RESTIC_PASSWORD_FILE=$PASSWORT_DATEI
    export RCLONE_CONFIG=$RCLONE_CONF
    export RESTIC_CACHE_DIR=/var/cache/finanz-o-mat-restic
}

status_schreiben() {
    # Stand für die App (Einstellungen > Backup); Zahlen aus dem letzten Lauf bleiben bei einem Fehler stehen
    local zustand=$1 meldung=${2:-} ziel=""
    install -d -o "$BENUTZER" -g "$BENUTZER" -m 750 "$STATUS_DIR" 2>/dev/null || return 0
    [[ -n ${BACKUP_URL:-} ]] && ziel="${BACKUP_URL#*://}"
    [[ -n $ziel ]] && ziel="${ziel%%/*} / ${BACKUP_ORDNER:-}"
    ZUSTAND=$zustand MELDUNG=$meldung ZIEL=$ziel STAENDE=${STAENDE:-} GROESSE=${GROESSE:-} \
        python3 - "$STATUS_DIR/status.json" <<'PY'
import json, os, re, sys
from datetime import datetime
e = os.environ
ziel = sys.argv[1]
try:
    with open(ziel, encoding="utf-8") as f:
        alt = json.load(f)
except (OSError, ValueError):
    alt = {}
jetzt = datetime.now().astimezone().isoformat(timespec="seconds")
daten = {
    "zustand": e["ZUSTAND"],  # aus, laeuft, ok, fehler
    "meldung": e["MELDUNG"],
    "ziel": e["ZIEL"] or alt.get("ziel"),
    "letzter_versuch": jetzt if e["ZUSTAND"] in ("ok", "fehler") else alt.get("letzter_versuch"),
    "letzte_sicherung": alt.get("letzte_sicherung"),
    "anzahl": alt.get("anzahl"),
    "groesse": alt.get("groesse"),
}
if e["STAENDE"]:
    staende = json.loads(e["STAENDE"]) or []
    daten["anzahl"] = len(staende)
    if staende:
        # restic liefert Nanosekunden, Browser verstehen höchstens Millisekunden
        neuester = max(staende, key=lambda s: s["time"])["time"]
        daten["letzte_sicherung"] = re.sub(r"\.\d+", "", neuester)
if e["GROESSE"]:
    daten["groesse"] = int(e["GROESSE"])
with open(ziel + ".neu", "w", encoding="utf-8") as f:
    json.dump(daten, f, ensure_ascii=False, indent=1)
os.chmod(ziel + ".neu", 0o644)
os.replace(ziel + ".neu", ziel)
PY
}

pakete() {
    command -v restic >/dev/null && command -v rclone >/dev/null && return 0
    info "Installiere restic und rclone"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends restic rclone ca-certificates >/dev/null
}

frage() {
    # frage VAR "Text" [geheim]: nur fragen, wenn der Wert fehlt und jemand am Terminal sitzt
    local var=$1 text=$2 geheim=${3:-} wert
    [[ -n ${!var:-} ]] && return 0
    [[ -t 0 ]] || abbruch "$var fehlt (ohne Terminal als Umgebungsvariable setzen)"
    if [[ -n $geheim ]]; then
        read -r -s -p "$text: " wert
        echo
    else
        read -r -p "$text: " wert
    fi
    printf -v "$var" '%s' "$wert"
}

einrichten() {
    root_pruefen
    pakete
    local webdav kodiert neues_passwort="" geschrieben="" repo_da=""
    if [[ -f $BACKUP_ENV ]]; then
        info "Backup ist schon eingerichtet ($(sed -n 's/^BACKUP_URL=//p' "$BACKUP_ENV")), Werte werden überschrieben"
    fi
    BACKUP_URL=${BACKUP_URL:-}
    BACKUP_BENUTZER=${BACKUP_BENUTZER:-}
    BACKUP_APP_PASSWORT=${BACKUP_APP_PASSWORT:-}
    frage BACKUP_URL "Nextcloud-Adresse (z. B. https://nx12345.your-storageshare.de)"
    frage BACKUP_BENUTZER "Nextcloud-Benutzer"
    frage BACKUP_APP_PASSWORT "App-Passwort (Nextcloud: Persönliche Einstellungen > Sicherheit > Neues App-Passwort)" geheim
    if [[ -z ${BACKUP_ORDNER:-} ]]; then
        if [[ -t 0 ]]; then
            read -r -p "Ordner in der Nextcloud [finanz-o-mat-backup]: " BACKUP_ORDNER
        fi
        BACKUP_ORDNER=${BACKUP_ORDNER:-finanz-o-mat-backup}
    fi
    BACKUP_URL=${BACKUP_URL%/}
    [[ $BACKUP_URL == http*://* ]] || BACKUP_URL="https://$BACKUP_URL"
    # Nextcloud-WebDAV liegt unter /remote.php/dav/files/<Benutzer>; eine komplette WebDAV-Adresse bleibt, wie sie ist
    if [[ $BACKUP_URL == */remote.php/* ]]; then
        webdav=$BACKUP_URL
    else
        kodiert=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$BACKUP_BENUTZER")
        webdav="$BACKUP_URL/remote.php/dav/files/$kodiert"
    fi

    umask 077
    install -d -m 750 "$KONFIG"
    {
        echo "[nextcloud]"
        echo "type = webdav"
        echo "url = $webdav"
        echo "vendor = nextcloud"
        echo "user = $BACKUP_BENUTZER"
        echo "pass = $(printf '%s' "$BACKUP_APP_PASSWORT" | rclone obscure -)"
    } >"$RCLONE_CONF.neu"
    info "Teste die Verbindung zur Nextcloud"
    if ! rclone --config "$RCLONE_CONF.neu" mkdir "nextcloud:$BACKUP_ORDNER" 2>/dev/null \
        || ! rclone --config "$RCLONE_CONF.neu" lsf --max-depth 1 "nextcloud:$BACKUP_ORDNER" >/dev/null 2>&1; then
        rm -f "$RCLONE_CONF.neu"
        abbruch "Keine Verbindung zu $webdav (Adresse, Benutzer oder App-Passwort falsch?)"
    fi
    mv "$RCLONE_CONF.neu" "$RCLONE_CONF"
    ok "Verbindung steht"

    rclone --config "$RCLONE_CONF" lsf "nextcloud:$BACKUP_ORDNER/config" >/dev/null 2>&1 && repo_da=1
    if [[ -n ${BACKUP_PASSWORT:-} ]]; then
        printf '%s\n' "$BACKUP_PASSWORT" >"$PASSWORT_DATEI"
        geschrieben=1
    elif [[ -s $PASSWORT_DATEI ]]; then
        : # vorhandenes Passwort behalten
    elif [[ -n $repo_da ]]; then
        # z. B. neuer Container nach einem Ausfall: an das vorhandene Backup anknüpfen
        frage BACKUP_PASSWORT "Im Ordner liegt schon ein Backup. Dessen Backup-Passwort" geheim
        printf '%s\n' "$BACKUP_PASSWORT" >"$PASSWORT_DATEI"
        geschrieben=1
    else
        python3 -c 'import secrets; print(secrets.token_urlsafe(24))' >"$PASSWORT_DATEI"
        neues_passwort=1
    fi
    chmod 600 "$PASSWORT_DATEI"
    printf 'BACKUP_URL=%q\nBACKUP_ORDNER=%q\n' "$BACKUP_URL" "$BACKUP_ORDNER" >"$BACKUP_ENV"
    umask 022

    repo_umgebung
    if restic cat config >/dev/null 2>&1; then
        ok "Vorhandenes Backup in $BACKUP_ORDNER gefunden"
    elif [[ -n $repo_da ]]; then
        [[ -n $geschrieben ]] && rm -f "$PASSWORT_DATEI" "$BACKUP_ENV"
        abbruch "In $BACKUP_ORDNER liegt ein Backup mit anderem Passwort. Richtiges Passwort über BACKUP_PASSWORT angeben oder anderen Ordner wählen."
    else
        restic init >/dev/null || abbruch "Backup-Speicher in $BACKUP_ORDNER konnte nicht angelegt werden"
        ok "Backup-Speicher in $BACKUP_ORDNER angelegt (verschlüsselt)"
    fi
    if hat_systemd; then
        systemctl enable --quiet --now "$DIENST-backup.timer" "$DIENST-backup.path" 2>/dev/null \
            || warn "Timer fehlt noch, einmal finanz-o-mat-update --force ausführen"
    fi
    if [[ -n $neues_passwort ]]; then
        echo
        warn "Backup-Passwort (bitte im Passwortmanager ablegen, ohne es sind die Backups wertlos):"
        echo
        echo "    $(cat "$PASSWORT_DATEI")"
        echo "    (steht auch in $PASSWORT_DATEI)"
        echo
    fi
    jetzt
    ok "Backup läuft ab jetzt täglich. Stand in der App unter Einstellungen > Backup."
}

inhalt_vorbereiten() {
    local db
    db=$(datenbank)
    install -d -m 700 "$INHALT"
    rm -rf "${INHALT:?}"/*
    [[ -f $db ]] || abbruch "Datenbank $db nicht gefunden"
    # Online-Sicherung über die SQLite-API, funktioniert auch bei laufendem Dienst
    python3 - "$db" "$INHALT/finanzomat.sqlite" <<'PY'
import sqlite3, sys
quelle, ziel = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with ziel:
    quelle.backup(ziel)
quelle.close()
ziel.close()
PY
    cp "$ENV_DATEI" "$INHALT/finanz-o-mat.env"
}

jetzt() {
    root_pruefen
    exec 8>"$SPERRE"
    flock -n 8 || { warn "Ein Backup läuft schon"; return 0; }
    rm -f "$STATUS_DIR/anfordern"
    if [[ ! -f $BACKUP_ENV ]]; then
        status_schreiben aus
        ok "Backup nicht eingerichtet (finanz-o-mat-backup einrichten)"
        return 0
    fi
    repo_umgebung
    export BACKUP_LAEUFT=1
    status_schreiben laeuft
    info "Erstelle Backup nach $BACKUP_ORDNER"
    inhalt_vorbereiten
    local zusammenfassung
    zusammenfassung=$(restic backup --json --host "$HOST" --tag auto "$INHALT" | tail -n1) \
        || abbruch "Upload zur Nextcloud fehlgeschlagen"
    GROESSE=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1]).get("total_bytes_processed", ""))' \
        "$zusammenfassung" 2>/dev/null || true)
    restic forget --quiet --host "$HOST" "${BEHALTEN[@]}" --prune >/dev/null \
        || abbruch "Alte Backups konnten nicht aufgeräumt werden"
    STAENDE=$(restic snapshots --json --host "$HOST") || abbruch "Liste der Backups nicht abrufbar"
    rm -rf "${INHALT:?}"/*
    unset BACKUP_LAEUFT
    status_schreiben ok
    ok "Backup fertig"
}

liste() {
    root_pruefen
    repo_umgebung
    restic snapshots --host "$HOST" --compact
}

wiederherstellen() {
    root_pruefen
    repo_umgebung
    local id=latest mit_konfig="" ja="" tmp quelle db antwort
    for a in "$@"; do
        case $a in
            --mit-konfig) mit_konfig=1 ;;
            -y | --yes) ja=1 ;;
            -*) abbruch "Unbekannte Option: $a" ;;
            *) id=$a ;;
        esac
    done
    [[ -n $ja || -t 0 ]] || abbruch "Ohne Terminal mit -y bestätigen"
    tmp=$(mktemp -d)
    info "Hole Backup $id"
    restic restore "$id" --host "$HOST" --target "$tmp" >/dev/null || { rm -rf "$tmp"; abbruch "Backup $id nicht lesbar"; }
    quelle=$tmp$INHALT
    [[ -f $quelle/finanzomat.sqlite ]] || { rm -rf "$tmp"; abbruch "Im Backup ist keine Datenbank"; }
    restic snapshots --host "$HOST" --compact "$id" 2>/dev/null | sed -n '3p' || true
    db=$(datenbank)
    if [[ -z $ja ]]; then
        read -r -p "Datenbank $db durch dieses Backup ersetzen? Die jetzige wird vorher gesichert. [j/N] " antwort
        [[ $antwort == [jJyY]* ]] || { rm -rf "$tmp"; echo "Abgebrochen."; return 0; }
    fi
    hat_systemd && systemctl stop "$DIENST"
    if [[ -f $db ]]; then
        install -d -o "$BENUTZER" -g "$BENUTZER" -m 750 "$DATEN/sicherungen"
        cp -p "$db" "$DATEN/sicherungen/vor-wiederherstellung-$(date +%Y%m%d-%H%M%S).sqlite"
    fi
    rm -f "$db-wal" "$db-shm"
    install -o "$BENUTZER" -g "$BENUTZER" -m 640 "$quelle/finanzomat.sqlite" "$db"
    if [[ -n $mit_konfig ]]; then
        install -o root -g "$BENUTZER" -m 640 "$quelle/finanz-o-mat.env" "$ENV_DATEI"
        ok "Konfiguration zurückgespielt"
    else
        install -o root -g root -m 600 "$quelle/finanz-o-mat.env" "$ENV_DATEI.aus-backup"
        info "Konfiguration aus dem Backup zum Vergleich: $ENV_DATEI.aus-backup (übernehmen mit --mit-konfig)"
    fi
    rm -rf "$tmp"
    hat_systemd && systemctl start "$DIENST"
    ok "Datenbank aus Backup $id zurückgespielt"
}

main() {
    case ${1:-jetzt} in
        einrichten) einrichten ;;
        jetzt) jetzt ;;
        liste) liste ;;
        wiederherstellen) shift; wiederherstellen "$@" ;;
        *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
    esac
}

main "$@"
exit
