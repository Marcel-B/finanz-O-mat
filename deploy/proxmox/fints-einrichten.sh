#!/usr/bin/env bash
# Läuft im Container: fragt die Zugangsdaten für FinTS (ING-Depot und ING-Konten) ab, trägt sie in die Konfiguration
# ein, startet den Dienst neu und ruft einmal zur Probe ab.
#
#   finanz-o-mat-fints              (nach einem Update installiert)
#   bash deploy/proxmox/fints-einrichten.sh
#
# Abgefragt werden FINTS_PRODUKT_ID (Registrierungsnummer der Deutschen Kreditwirtschaft), ING_FINTS_ZUGANG
# (Zugangsnummer, die letzten 10 Stellen der IBAN) und ING_FINTS_PIN (PIN fürs Internetbanking). Produkt-ID und PIN
# werden verdeckt eingegeben und nie ausgegeben; sie landen nur in /etc/finanz-o-mat/finanz-o-mat.env (root und
# Dienstbenutzer, 640). Eingabetaste ohne Wert behält einen schon eingetragenen Wert.

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
    # Wert einer Variable aus der Konfiguration, ohne Anführungszeichen
    sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}\$/\1/p" "$ENV_DATEI" | tail -n1
}

zulaessig() {
    # Die Datei wird von der Shell gelesen (finanz-o-mat) und von systemd: keine " \ $ ` und kein Zeilenumbruch
    [[ $1 != *[\"\\\$\`]* && $1 != *$'\n'* ]]
}

fragen() {
    # fragen VARIABLE "Text" verdeckt|offen → neuer Wert in ANTWORT (leer: bisherigen behalten)
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
        if [[ $art == verdeckt ]]; then
            read -r -s -u 3 -p "$text: " eingabe
            echo
        else
            read -r -u 3 -p "$text: " eingabe
        fi
        eingabe=${eingabe//[[:space:]]/}
        if [[ -z $eingabe ]]; then
            [[ -n $bisher ]] && { ANTWORT=$bisher; return; }
            warn "Bitte einen Wert eingeben."
            continue
        fi
        zulaessig "$eingabe" || { warn "Bitte ohne \" \\ \$ \` eingeben."; continue; }
        ANTWORT=$eingabe
        return
    done
}

info "FinTS-Zugang für die ING einrichten ($ENV_DATEI)"
echo "Produkt-ID und PIN werden beim Tippen nicht angezeigt."
echo

fragen FINTS_PRODUKT_ID "Produkt-ID (Registrierungsnummer aus der Mail der Deutschen Kreditwirtschaft)" verdeckt
produkt=$ANTWORT

while true; do
    fragen ING_FINTS_ZUGANG "Zugangsnummer (letzte 10 Stellen der IBAN, oder die ganze IBAN)" offen
    zugang=${ANTWORT^^}
    # Aus einer ganzen IBAN die Kontonummer nehmen: DE.. 50010517 + 10 Stellen
    [[ $zugang =~ ^DE[0-9]{2}50010517([0-9]{10})$ ]] && zugang=${BASH_REMATCH[1]}
    [[ $zugang =~ ^[0-9]{10}$ ]] && break
    warn "Die Zugangsnummer hat 10 Ziffern (bzw. eine IBAN der ING mit 22 Zeichen)."
done

fragen ING_FINTS_PIN "PIN fürs Internetbanking" verdeckt
pin=$ANTWORT

# In eine Kopie neben der Datei schreiben und tauschen; Besitzer und Rechte bleiben. Die Werte gehen über die
# Umgebung an awk, nicht über die Befehlszeile (sonst stünden sie kurz in der Prozessliste).
neu=$(mktemp "$ENV_DATEI.XXXXXX")
trap 'rm -f "$neu"' EXIT
chmod --reference="$ENV_DATEI" "$neu"
chown --reference="$ENV_DATEI" "$neu"
FINTS_PRODUKT_ID=$produkt ING_FINTS_ZUGANG=$zugang ING_FINTS_PIN=$pin awk '
    BEGIN { n = split("FINTS_PRODUKT_ID ING_FINTS_ZUGANG ING_FINTS_PIN", namen, " ") }
    {
        for (i = 1; i <= n; i++) {
            if (index($0, namen[i] "=") == 1) {
                if (!(namen[i] in gesetzt)) print namen[i] "=\"" ENVIRON[namen[i]] "\""
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
        for (i = 1; i <= n; i++) if (!(namen[i] in gesetzt)) print namen[i] "=\"" ENVIRON[namen[i]] "\""
    }
' "$ENV_DATEI" >"$neu"
mv "$neu" "$ENV_DATEI"
trap - EXIT
unset produkt pin
ok "Eingetragen: Produkt-ID, Zugangsnummer …${zugang: -4}, PIN"

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
