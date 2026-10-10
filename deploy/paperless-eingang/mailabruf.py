"""Mail-Abruf in Paperless-ngx einrichten: IMAP-Konto, Regeln je Rechnungsordner, Tags.

Wird von paperless-eingang.sh aufgerufen (nur Standardbibliothek, läuft mit jedem Python 3.9+). Fragt interaktiv
nach; Vorgaben kommen aus Umgebungsvariablen (PAPERLESS_URL, PAPERLESS_TOKEN, MAIL_ADRESSE, MAIL_PASSWORT, IMAP_SERVER,
IMAP_PORT, KONTO_NAME, RECHNUNGS_ORDNER, POSTEINGANG_MUSTER, MAX_ALTER, TAGS, MAIL_AKTION=markieren|schluesselwort).
Erneut ausführen ist gefahrlos: Konto, Regeln und Tags werden über ihren Namen gefunden und aktualisiert.
"""

from __future__ import annotations

import getpass
import imaplib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KENNWORT = "paperless"  # IMAP-Schlüsselwort für schon abgeholte Mails
REGEL_PRAEFIX = "Rechnungen: "
POSTEINGANG_PRAEFIX = "Posteingang: "
# Paperless-Werte (paperless_mail/models.py)
SSL = 2
AKTION_FLAG = 4
AKTION_TAG = 5
TITEL_AUS_BETREFF = 1
KORRESPONDENT_NICHT = 1
NUR_ANHAENGE = 1


def info(text: str) -> None:
    print(f"\033[1;34m==>\033[0m {text}")


def ok(text: str) -> None:
    print(f"\033[1;32m ok\033[0m {text}")


def warn(text: str) -> None:
    print(f"\033[1;33m !!\033[0m {text}", file=sys.stderr)


def abbruch(text: str) -> None:
    print(f"\033[1;31mFehler:\033[0m {text}", file=sys.stderr)
    sys.exit(1)


def frage(text: str, vorgabe: str = "", geheim: bool = False) -> str:
    if geheim:
        hinweis = " (Enter = behalten)" if vorgabe else ""
        eingabe = getpass.getpass(f"{text}{hinweis}: ")
    else:
        eingabe = input(f"{text} [{vorgabe}]: ")
    return eingabe.strip() or vorgabe


def ja(text: str, vorgabe: bool = True) -> bool:
    antwort = input(f"{text} [{'J/n' if vorgabe else 'j/N'}]: ").strip()
    return vorgabe if not antwort else antwort[:1] in "JjYy"


# --- Paperless-API ----------------------------------------------------------------------------------------------


class Paperless:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def anfrage(self, methode: str, pfad: str, daten: dict | None = None) -> dict | list:
        req = urllib.request.Request(
            self.url + pfad,
            method=methode,
            data=json.dumps(daten).encode() if daten is not None else None,
            headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
        )
        if daten is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as antwort:
                inhalt = antwort.read()
        except urllib.error.HTTPError as fehler:
            text = fehler.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{methode} {pfad}: HTTP {fehler.code} {text}") from None
        return json.loads(inhalt) if inhalt else {}

    def alle(self, pfad: str) -> list[dict]:
        ergebnis: list[dict] = []
        naechste: str | None = f"{pfad}?page_size=100"
        while naechste:
            seite = self.anfrage("GET", naechste)
            ergebnis += seite["results"]
            naechste = seite.get("next")
            if naechste:
                teile = urllib.parse.urlsplit(naechste)
                naechste = f"{teile.path}?{teile.query}"
        return ergebnis

    def anlegen_oder_aendern(self, pfad: str, vorhandene: list[dict], daten: dict) -> dict:
        treffer = next((o for o in vorhandene if o["name"].lower() == daten["name"].lower()), None)
        if treffer:
            return self.anfrage("PATCH", f"{pfad}{treffer['id']}/", daten)
        return self.anfrage("POST", pfad, daten)

    def tag(self, name: str, vorhandene: list[dict]) -> int:
        treffer = next((t for t in vorhandene if t["name"].lower() == name.lower()), None)
        if treffer:
            return treffer["id"]
        neu = self.anfrage("POST", "/api/tags/", {"name": name, "matching_algorithm": 0})
        ok(f"Tag „{name}“ angelegt")
        return neu["id"]


# --- IMAP -------------------------------------------------------------------------------------------------------

LIST_ZEILE = re.compile(rb'\((?P<flags>[^)]*)\) (?P<trenner>"[^"]*"|NIL) (?P<name>.+)$')


def imap_ordner(server: str, port: int, benutzer: str, passwort: str) -> tuple[list[str], bool]:
    """Ordnernamen (so wie Paperless sie braucht) und ob der Server eigene Schlüsselwörter speichern kann."""
    with imaplib.IMAP4_SSL(server, port, timeout=30) as imap:
        imap.login(benutzer, passwort)
        _, zeilen = imap.list()
        ordner = []
        for zeile in zeilen:
            if not isinstance(zeile, bytes):
                continue
            treffer = LIST_ZEILE.match(zeile)
            if not treffer or b"\\Noselect" in treffer["flags"]:
                continue
            name = treffer["name"].strip()
            if name.startswith(b'"') and name.endswith(b'"'):
                name = name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
            ordner.append(imap_utf7_decode(name))
        imap.select("INBOX", readonly=True)
        # z. B. "[PERMANENTFLAGS (\Answered \Flagged \Deleted \Seen \Draft \*)]"; "\*" = beliebige Schlüsselwörter
        antworten = imap.untagged_responses.get("OK", [])
        flags = " ".join(x.decode(errors="replace") if isinstance(x, bytes) else str(x) for x in antworten)
        treffer = re.search(r"PERMANENTFLAGS \(([^)]*)\)", flags)
        kennwoerter = bool(treffer) and "\\*" in treffer.group(1).split()
    return sorted(ordner, key=lambda n: (n.upper() != "INBOX", n.lower())), kennwoerter


def imap_utf7_decode(roh: bytes) -> str:
    """Modified UTF-7 (RFC 3501) in Klartext, z. B. „Entw&APw-rfe“ -> „Entwürfe“."""
    import base64

    text = roh.decode("ascii", errors="replace")
    teile = re.split(r"(&[^-]*-)", text)
    aus = []
    for teil in teile:
        if teil == "&-":
            aus.append("&")
        elif teil.startswith("&") and teil.endswith("-"):
            b64 = teil[1:-1].replace(",", "/")
            b64 += "=" * (-len(b64) % 4)
            aus.append(base64.b64decode(b64).decode("utf-16-be"))
        else:
            aus.append(teil)
    return "".join(aus)


def waehle_ordner(ordner: list[str], vorgabe: list[str]) -> list[str]:
    print()
    for i, name in enumerate(ordner, 1):
        markiert = "*" if name in vorgabe else " "
        print(f"  {markiert}{i:3}  {name}")
    print()
    vorgabe_nr = ",".join(str(ordner.index(n) + 1) for n in vorgabe if n in ordner)
    while True:
        eingabe = frage("Ordner mit Rechnungen (Nummern mit Komma, z. B. 4,7,9)", vorgabe_nr)
        try:
            nummern = [int(x) for x in re.split(r"[,\s]+", eingabe) if x]
            gewaehlt = [ordner[n - 1] for n in nummern if 1 <= n <= len(ordner)]
        except ValueError:
            gewaehlt = []
        if gewaehlt:
            return gewaehlt
        warn("Bitte mindestens eine Nummer aus der Liste angeben.")


# --- Ablauf -----------------------------------------------------------------------------------------------------


def regel_daten(name: str, konto: int, ordner: str, muster: str, alter: int, aktion: int, tags: list[int]) -> dict:
    return {
        "name": name,
        "account": konto,
        "folder": ordner,
        "enabled": True,
        "filter_attachment_filename_include": muster,
        "maximum_age": alter,
        "action": aktion,
        "action_parameter": KENNWORT if aktion == AKTION_TAG else "",
        "assign_title_from": TITEL_AUS_BETREFF,
        # Korrespondent und weitere Tags erkennt Paperless selbst ("Automatisch"-Zuordnung),
        # damit keine Dubletten wie "billing@anbieter.de" neben "Anbieter GmbH" entstehen
        "assign_correspondent_from": KORRESPONDENT_NICHT,
        "assign_tags": tags,
        "attachment_type": NUR_ANHAENGE,
        "consumption_scope": NUR_ANHAENGE,
    }


def main() -> None:
    env = os.environ
    url = frage("Paperless-Adresse", env.get("PAPERLESS_URL", "http://localhost:8000"))
    token = env.get("PAPERLESS_TOKEN") or frage("Paperless-API-Token (Profil > API-Auth-Token)", geheim=True)
    if not token:
        abbruch("Ohne API-Token geht es nicht.")
    pl = Paperless(url, token)
    try:
        konten = pl.alle("/api/mail_accounts/")
    except (RuntimeError, OSError) as fehler:
        abbruch(f"Paperless unter {url} nicht erreichbar oder Token falsch: {fehler}")
    ok(f"Paperless erreichbar ({len(konten)} Mailkonto/-konten vorhanden)")

    konto_name = env.get("KONTO_NAME", "Strato")
    vorhanden = next((k for k in konten if k["name"] == konto_name), None)
    adresse = frage("E-Mail-Adresse", env.get("MAIL_ADRESSE") or (vorhanden or {}).get("username", ""))
    server = frage("IMAP-Server", env.get("IMAP_SERVER") or (vorhanden or {}).get("imap_server", "imap.strato.de"))
    port = int(env.get("IMAP_PORT") or (vorhanden or {}).get("imap_port") or 993)
    passwort = env.get("MAIL_PASSWORT") or frage("Passwort des Postfachs", geheim=True)
    if not adresse or not passwort:
        abbruch("Adresse und Passwort werden gebraucht.")

    info(f"Melde mich bei {server} an und lese die Ordner")
    try:
        ordner, kennwoerter = imap_ordner(server, port, adresse, passwort)
    except (imaplib.IMAP4.error, OSError) as fehler:
        abbruch(f"Anmeldung bei {server} fehlgeschlagen: {fehler}")
    ok(f"{len(ordner)} Ordner gefunden")

    regeln = pl.alle("/api/mail_rules/")
    bisher = [r["folder"] for r in regeln if r["name"].startswith(REGEL_PRAEFIX)]
    vorgabe = [o for o in env.get("RECHNUNGS_ORDNER", "").split(",") if o] or bisher
    gewaehlt = waehle_ordner(ordner, vorgabe)
    ok("Rechnungsordner: " + ", ".join(gewaehlt))

    muster_vorgabe = env.get("POSTEINGANG_MUSTER", "*rechnung*.pdf,*invoice*.pdf,*receipt*.pdf,*quittung*.pdf")
    print(
        "\nFalsch einsortierte Rechnungen: Paperless kann zusätzlich den Posteingang nach PDF-Anhängen durchsuchen,\n"
        "deren Dateiname nach Rechnung aussieht."
    )
    muster = []
    if "INBOX" in ordner and ja("Posteingang so mit durchsuchen?", True):
        muster = [m.strip() for m in frage("Dateinamen-Muster", muster_vorgabe).split(",") if m.strip()]

    alter_text = frage("Wie viele Tage zurück beim ersten Abruf", env.get("MAX_ALTER", "30"))
    if not alter_text.isdigit():
        abbruch("Bitte die Tage als Zahl angeben.")
    alter = int(alter_text)
    tag_text = frage("Tags für abgeholte Rechnungen", env.get("TAGS", "Eingangsrechnung,E-Mail"))
    tag_namen = [t.strip() for t in tag_text.split(",") if t.strip()]

    aktion_env = env.get("MAIL_AKTION", "")
    if aktion_env == "markieren" or (not aktion_env and not kennwoerter):
        aktion = AKTION_FLAG
        warn(f"{server} speichert keine eigenen Schlüsselwörter; abgeholte Mails werden deshalb mit Stern markiert.")
    else:
        aktion = AKTION_TAG
        ok(f"Abgeholte Mails bekommen das unsichtbare Schlüsselwort „{KENNWORT}“, sonst ändert sich nichts an ihnen")

    # Tags, Konto, Regeln
    alle_tags = pl.alle("/api/tags/")
    tag_ids = [pl.tag(n, alle_tags) for n in tag_namen]

    try:
        pl.anfrage(
            "POST",
            "/api/mail_accounts/test/",
            {"imap_server": server, "imap_port": port, "imap_security": SSL, "username": adresse, "password": passwort},
        )
    except RuntimeError as fehler:
        abbruch(f"Paperless kommt mit diesen Zugangsdaten nicht ins Postfach: {fehler}")
    konto = pl.anlegen_oder_aendern(
        "/api/mail_accounts/",
        konten,
        {
            "name": konto_name,
            "imap_server": server,
            "imap_port": port,
            "imap_security": SSL,
            "username": adresse,
            "password": passwort,
            "character_set": "UTF-8",
        },
    )
    ok(f"Mailkonto „{konto_name}“ eingerichtet")

    soll: list[dict] = []
    for i, name in enumerate(gewaehlt):
        regel = regel_daten(REGEL_PRAEFIX + name, konto["id"], name, "*.pdf", alter, aktion, tag_ids)
        soll.append(regel | {"order": 10 + i})
    for i, m in enumerate(muster):
        regel = regel_daten(POSTEINGANG_PRAEFIX + m, konto["id"], "INBOX", m, alter, aktion, tag_ids)
        soll.append(regel | {"order": 100 + i})

    soll_namen = {r["name"] for r in soll}
    for regel in soll:
        pl.anlegen_oder_aendern("/api/mail_rules/", regeln, regel)
        ok(f"Regel „{regel['name']}“")
    for regel in regeln:
        eigene = regel["name"].startswith((REGEL_PRAEFIX, POSTEINGANG_PRAEFIX)) and regel.get("account") == konto["id"]
        if eigene and regel["name"] not in soll_namen and regel.get("enabled"):
            pl.anfrage("PATCH", f"/api/mail_rules/{regel['id']}/", {"enabled": False})
            ok(f"Regel „{regel['name']}“ abgeschaltet (nicht mehr gewählt)")

    if ja("Jetzt gleich abrufen statt auf den nächsten Lauf (alle 10 Minuten) zu warten?", True):
        try:
            pl.anfrage("POST", f"/api/mail_accounts/{konto['id']}/process/", {})
            ok("Abruf gestartet; Ergebnis in Paperless unter Dokumente bzw. E-Mail > Verarbeitete Mails")
        except RuntimeError as fehler:
            warn(f"Sofort-Abruf ging nicht ({fehler}); Paperless holt die Mails beim nächsten Lauf.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except RuntimeError as fehler:
        abbruch(str(fehler))
