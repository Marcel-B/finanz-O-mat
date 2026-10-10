"""Einstellungen aus der Oberfläche und Zugangsdaten.

Die Umgebungsvariablen (bzw. die .env-Datei) liefern die Grundwerte. Was in der
Oberfläche gespeichert wird, liegt in der Datenbank und hat Vorrang. Ein leerer
bzw. entfernter Wert in der Datenbank lässt wieder die Umgebungsvariable gelten.

Das Passwort für die Anmeldung wird nur als PBKDF2-Hash gespeichert. Ist ein
Hash hinterlegt, gilt er statt ``APP_PASSWORT``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import replace

from .config import Config
from .db import Datenbank

# Schlüssel in der Datenbank -> Feld in Config
TEXTE = ("paperless_url", "paperless_token", "eigene_ustid", "bundesland", "paperless_tag_kontoauszug",
         "paperless_tag_protokoll")
LISTEN = ("paperless_tags_ausgang", "paperless_tags_eingang")
BENUTZER = "app_benutzer"
PASSWORT_HASH = "app_passwort_hash"

BUNDESLAENDER = {
    "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin", "BB": "Brandenburg", "HB": "Bremen",
    "HH": "Hamburg", "HE": "Hessen", "MV": "Mecklenburg-Vorpommern", "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz", "SL": "Saarland", "SN": "Sachsen",
    "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein", "TH": "Thüringen",
}
RE_USTID = re.compile(r"^[A-Z]{2}[0-9A-Z+*]{2,12}$")
MIN_PASSWORT = 10
ITERATIONEN = 600_000


def _liste(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def wirksam(cfg: Config, db: Datenbank) -> Config:
    """Konfiguration mit den in der Oberfläche gespeicherten Werten."""
    gespeichert = db.einstellungen()
    werte: dict = {k: gespeichert[k] for k in TEXTE if gespeichert.get(k)}
    werte |= {k: _liste(gespeichert[k]) for k in LISTEN if gespeichert.get(k)}
    if gespeichert.get(BENUTZER):
        werte["app_benutzer"] = gespeichert[BENUTZER]
    return replace(cfg, **werte) if werte else cfg


def quelle(cfg: Config, db: Datenbank, feld: str) -> str:
    """Woher ein Wert stammt: "app", "umgebung" oder "" (nicht gesetzt)."""
    if db.einstellungen().get(feld):
        return "app"
    wert = getattr(cfg, feld)
    return "umgebung" if wert else ""


def pruefen(feld: str, wert: str) -> str:
    """Normalisiert und prüft einen Wert; wirft ValueError mit einer verständlichen Meldung."""
    wert = wert.strip()
    if feld == "paperless_url" and wert:
        if not re.match(r"^https?://[^\s/]+", wert):
            raise ValueError("Die Paperless-URL muss mit http:// oder https:// beginnen.")
        return wert.rstrip("/")
    if feld == "eigene_ustid" and wert:
        wert = wert.replace(" ", "").upper()
        if not RE_USTID.match(wert):
            raise ValueError("Die USt-IdNr. hat kein gültiges Format (z. B. DE123456789).")
    if feld == "bundesland" and wert:
        wert = wert.upper()
        if wert not in BUNDESLAENDER:
            raise ValueError("Unbekanntes Bundesland.")
    if feld in LISTEN:
        return ", ".join(_liste(wert))
    return wert


# Passwort

def passwort_hash(passwort: str, salz: bytes | None = None, iterationen: int = ITERATIONEN) -> str:
    salz = salz or secrets.token_bytes(16)
    wert = hashlib.pbkdf2_hmac("sha256", passwort.encode(), salz, iterationen)
    return f"pbkdf2_sha256${iterationen}${base64.b64encode(salz).decode()}${base64.b64encode(wert).decode()}"


def passwort_stimmt(passwort: str, gespeichert: str) -> bool:
    try:
        verfahren, iterationen, salz, wert = gespeichert.split("$")
        if verfahren != "pbkdf2_sha256":
            return False
        neu = hashlib.pbkdf2_hmac("sha256", passwort.encode(), base64.b64decode(salz), int(iterationen))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(neu, base64.b64decode(wert))


class Anmeldung:
    """Prüft HTTP-Basic-Anmeldungen gegen den gespeicherten Hash oder APP_BENUTZER/APP_PASSWORT.

    Erfolgreich geprüfte Kopfzeilen werden gemerkt, damit PBKDF2 nicht bei jeder
    Anfrage läuft. Nach einer Passwortänderung wird der Speicher geleert.
    """

    def __init__(self, cfg: Config, db: Datenbank):
        self.cfg, self.db = cfg, db
        # Kopfzeile -> Zugangsdaten, mit denen sie geprüft wurde (wird ungültig, sobald diese sich ändern,
        # auch wenn das Passwort über die Kommandozeile geändert wurde)
        self._gueltig: dict[bytes, str] = {}

    def zugang(self) -> tuple[str, str | None, str | None]:
        """(Benutzer, Hash, Klartext aus der Umgebung); ohne Passwort ist die Anmeldung aus."""
        e = self.db.einstellungen()
        benutzer = e.get(BENUTZER) or self.cfg.app_benutzer or "admin"
        if e.get(PASSWORT_HASH):
            return benutzer, e[PASSWORT_HASH], None
        if self.cfg.app_benutzer and self.cfg.app_passwort:
            return self.cfg.app_benutzer, None, self.cfg.app_passwort
        return benutzer, None, None

    @property
    def aktiv(self) -> bool:
        _, h, klartext = self.zugang()
        return bool(h or klartext)

    def quelle(self) -> str:
        return "app" if self.db.einstellungen().get(PASSWORT_HASH) else ("umgebung" if self.aktiv else "")

    def pruefe(self, benutzer: str, passwort: str) -> bool:
        erwartet, h, klartext = self.zugang()
        if not (h or klartext):
            return True
        if not hmac.compare_digest(benutzer.encode(), erwartet.encode()):
            return False
        if h:
            return passwort_stimmt(passwort, h)
        return hmac.compare_digest(passwort.encode(), (klartext or "").encode())

    def kopf_gueltig(self, kopf: str) -> bool:
        benutzer_soll, h, klartext = self.zugang()
        if not (h or klartext):
            return True
        stand = f"{benutzer_soll}:{h or hashlib.sha256((klartext or '').encode()).hexdigest()}"
        schluessel = hashlib.sha256(kopf.encode()).digest()
        if self._gueltig.get(schluessel) == stand:
            return True
        if not kopf.lower().startswith("basic "):
            return False
        try:
            benutzer, _, passwort = base64.b64decode(kopf[6:].strip()).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        if self.pruefe(benutzer, passwort):
            if len(self._gueltig) > 100:
                self._gueltig.clear()
            self._gueltig[schluessel] = stand
            return True
        return False

    def setzen(self, benutzer: str, passwort: str) -> None:
        if len(passwort) < MIN_PASSWORT:
            raise ValueError(f"Das Passwort muss mindestens {MIN_PASSWORT} Zeichen haben.")
        benutzer = benutzer.strip() or "admin"
        if ":" in benutzer:
            raise ValueError("Der Benutzername darf keinen Doppelpunkt enthalten.")
        self.db.einstellung_setzen(BENUTZER, benutzer)
        self.db.einstellung_setzen(PASSWORT_HASH, passwort_hash(passwort))
        self._gueltig.clear()

    def zuruecksetzen(self) -> None:
        """Entfernt Benutzer und Passwort aus der Datenbank; dann gilt wieder die Umgebung."""
        self.db.einstellung_setzen(BENUTZER, None)
        self.db.einstellung_setzen(PASSWORT_HASH, None)
        self._gueltig.clear()
