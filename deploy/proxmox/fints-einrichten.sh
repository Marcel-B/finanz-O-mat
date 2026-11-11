#!/usr/bin/env bash
# Läuft im Container: fragt die Zugangsdaten für FinTS (ING-Depot und ING-Konten) ab, trägt sie in die Konfiguration
# ein, startet den Dienst neu und ruft einmal zur Probe ab.
#
#   finanz-o-mat-fints              (nach einem Update installiert)
#   bash deploy/proxmox/fints-einrichten.sh
#
# Abgefragt werden FINTS_PRODUKT_ID (Registrierungsnummer der Deutschen Kreditwirtschaft), ING_FINTS_ZUGANG (der
# selbst gewählte Benutzername des Online-Bankings; vor der Umstellung der ING 2026 die Zugangsnummer) und
# ING_FINTS_PIN (das Passwort des Online-Bankings, vorher die Internetbanking-PIN; nicht die App-PIN, nicht der
# DiBa-Key). Produkt-ID und Passwort werden verdeckt eingegeben und nie ausgegeben; sie landen nur in
# /etc/finanz-o-mat/finanz-o-mat.env (root und Dienstbenutzer, 640). Eingabetaste ohne Wert behält einen schon
# eingetragenen Wert.

set -Eeuo pipefail

ENV_DATEI=${ENV_DATEI:-/etc/finanz-o-mat/finanz-o-mat.env}
DIENST=finanz-o-mat

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || abbruch "Bitte als root im Container ausführen (pct enter <CTID>)."
[[ -f $ENV_DATEI ]] || abbruch "$ENV_DATEI fehlt; ist finanz-O-mat in diesem Container installiert?"
# Auch über eine Pipe (curl … | bash) von der Tastatur lesen; EINGABE nur für Tests
exec 3<"${EINGABE:-/dev/tty}" || abbruch "Keine Tastatur: bitte in einer interaktiven Sitzung ausführen."

env_wert() {
    # Wert einer Variable aus der Konfiguration, ohne die Anführungszeichen drumherum
    local wert
    wert=$(grep "^$1=" "$ENV_DATEI" | tail -n1) || true
    wert=${wert#*=}
    if [[ $wert == \'*\' || $wert == \"*\" ]] && ((${#wert} >= 2)); then
        wert=${wert:1:${#wert}-2}
    fi
    printf '%s' "$wert"
}

zulaessig() {
    # Werte stehen in einfachen Anführungszeichen: Shell (finanz-o-mat) und systemd nehmen darin alles wörtlich,
    # nur ' selbst und Zeilenumbrüche gehen nicht
    [[ $1 != *\'* && $1 != *$'\n'* ]]
}

fragen() {
    # fragen VARIABLE "Text" verdeckt|offen|passwort → neuer Wert in ANTWORT (leer: bisherigen behalten);
    # ein Passwort bleibt wie getippt, sonst fallen Leerzeichen weg
    local name=$1 text=$2 art=$3 bisher eingabe
    bisher=$(env_wert "$name")
    if [[ -n $bisher ]]; then
        if [[ $art == offen ]]; then
            text+=" [Eingabetaste: …${bisher: -4} behalten]"
        else
            text+=" [Eingabetaste: gespeicherten Wert behalten]"
        fi
    fi
    while true; do
        if [[ $art != offen ]]; then
            IFS= read -r -s -u 3 -p "$text: " eingabe
            echo
        else
            IFS= read -r -u 3 -p "$text: " eingabe
        fi
        [[ $art == passwort ]] || eingabe=${eingabe//[[:space:]]/}
        if [[ -z $eingabe ]]; then
            [[ -n $bisher ]] && { ANTWORT=$bisher; return; }
            warn "Bitte einen Wert eingeben."
            continue
        fi
        zulaessig "$eingabe" || { warn "Ein ' kann die Konfiguration nicht aufnehmen."; continue; }
        ANTWORT=$eingabe
        return
    done
}

info "FinTS-Zugang für die ING einrichten ($ENV_DATEI)"
echo "Benutzername und Passwort sind die des Online-Bankings im Browser (nicht App-PIN, nicht DiBa-Key)."
echo "Produkt-ID und Passwort werden beim Tippen nicht angezeigt."
echo

fragen FINTS_PRODUKT_ID "Produkt-ID (Registrierungsnummer aus der Mail der Deutschen Kreditwirtschaft)" verdeckt
produkt=$ANTWORT

fragen ING_FINTS_ZUGANG "Benutzername des Online-Bankings (vor der Umstellung: Zugangsnummer)" offen
zugang=$ANTWORT

fragen ING_FINTS_PIN "Passwort des Online-Bankings (vor der Umstellung: Internetbanking-PIN)" passwort
pin=$ANTWORT

# In eine Kopie neben der Datei schreiben und tauschen; Besitzer und Rechte bleiben. Die Werte gehen über die
# Umgebung an awk, nicht über die Befehlszeile (sonst stünden sie kurz in der Prozessliste).
neu=$(mktemp "$ENV_DATEI.XXXXXX")
trap 'rm -f "$neu"' EXIT
chmod --reference="$ENV_DATEI" "$neu"
chown --reference="$ENV_DATEI" "$neu"
FINTS_PRODUKT_ID=$produkt ING_FINTS_ZUGANG=$zugang ING_FINTS_PIN=$pin awk -v q="'" '
    BEGIN { n = split("FINTS_PRODUKT_ID ING_FINTS_ZUGANG ING_FINTS_PIN", namen, " ") }
    {
        for (i = 1; i <= n; i++) {
            if (index($0, namen[i] "=") == 1) {
                if (!(namen[i] in gesetzt)) print namen[i] "=" q ENVIRON[namen[i]] q
                gesetzt[namen[i]] = 1
                next
            }
        }
        print
    }
    END {
        fehlt = 0
        for (i = 1; i <= n; i++) if (!(namen[i] in gesetzt)) fehlt = 1
        if (fehlt) print "\n# FinTS-Zugang zur ING (Depot und Konten), siehe README"
        for (i = 1; i <= n; i++) if (!(namen[i] in gesetzt)) print namen[i] "=" q ENVIRON[namen[i]] q
    }
' "$ENV_DATEI" >"$neu"
mv "$neu" "$ENV_DATEI"
trap - EXIT
unset produkt pin
ok "Eingetragen: Produkt-ID, Benutzername …${zugang: -3}, Passwort"

if [[ -d /run/systemd/system ]]; then
    systemctl restart "$DIENST"
    ok "Dienst $DIENST neu gestartet"
fi

echo
read -r -u 3 -p "Jetzt einmal abrufen? Die ING schickt dann eine Freigabe in die ING-App. [J/n] " probe
if [[ ${probe,,} != n* ]]; then
    befehl=$(command -v finanz-o-mat || echo /usr/local/bin/finanz-o-mat)
    info "Depot abrufen (finanz-o-mat bank depot)"
    "$befehl" bank depot || warn "Depotabruf fehlgeschlagen, siehe oben"
    if "$befehl" bank ing --help >/dev/null 2>&1; then
        echo
        info "ING-Konten abrufen (finanz-o-mat bank ing)"
        "$befehl" bank ing || warn "Abruf der ING-Konten fehlgeschlagen, siehe oben"
    fi
fi
