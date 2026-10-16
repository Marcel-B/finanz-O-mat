"""Einstellungen aus der Oberfläche und Zugangsdaten.

Die Umgebungsvariablen (bzw. die .env-Datei) liefern die Grundwerte. Was in der
Oberfläche gespeichert wird, liegt in der Datenbank und hat Vorrang. Ein leerer
bzw. entfernter Wert in der Datenbank lässt wieder die Umgebungsvariable gelten.

Das Passwort für die Anmeldung wird nur als PBKDF2-Hash gespeichert. Ist ein
Hash hinterlegt, gilt er statt ``APP_PASSWORT``. Angemeldet wird über ein
Formular in der Oberfläche; danach trägt der Browser ein Sitzungs-Cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import re
import secrets
import threading
import time
from dataclasses import replace
from functools import lru_cache

from .config import Config
from .db import Datenbank

# Schlüssel in der Datenbank -> Feld in Config
TEXTE = ("paperless_url", "paperless_token", "eigene_ustid", "bundesland", "paperless_tag_kontoauszug",
         "paperless_tag_protokoll", "kalender_url", "kalender_stichwort")
LISTEN = ("paperless_tags_ausgang", "paperless_tags_eingang")
BENUTZER = "app_benutzer"
PASSWORT_HASH = "app_passwort_hash"
SITZUNG_SALZ = "sitzung_salz"

BUNDESLAENDER = {
    "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin", "BB": "Brandenburg", "HB": "Bremen",
    "HH": "Hamburg", "HE": "Hessen", "MV": "Mecklenburg-Vorpommern", "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz", "SL": "Saarland", "SN": "Sachsen",
    "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein", "TH": "Thüringen",
}
RE_USTID = re.compile(r"^[A-Z]{2}[0-9A-Z+*]{2,12}$")
MIN_PASSWORT = 10
ITERATIONEN = 600_000

COOKIE = "finanzomat_sitzung"
SITZUNG_LEERLAUF = 8 * 3600  # ohne Nutzung nach 8 Stunden abgemeldet
SITZUNG_HOECHSTENS = 7 * 86400  # spätestens nach 7 Tagen neu anmelden
FEHLVERSUCHE = 5  # je Adresse innerhalb von SPERRFENSTER, dann SPERRDAUER gesperrt
FEHLVERSUCHE_GESAMT = 20  # von allen Adressen zusammen
SPERRFENSTER = 15 * 60
SPERRDAUER = 15 * 60

log = logging.getLogger(__name__)
# Uhren; in Tests ersetzbar
_jetzt = time.time
_monoton = time.monotonic


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
    if feld == "kalender_url" and wert:
        if not re.match(r"^(https?|webcals?)://[^\s/]+", wert, re.IGNORECASE):
            raise ValueError("Die Kalender-Adresse muss mit https:// oder webcal:// beginnen.")
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


class Gesperrt(Exception):
    """Zu viele Fehlversuche; ``sekunden`` bis zum nächsten erlaubten Versuch."""

    def __init__(self, sekunden: int):
        super().__init__(sekunden)
        self.sekunden = sekunden


class Sperre:
    """Bremst das Durchprobieren von Passwörtern.

    Nach FEHLVERSUCHE falschen Versuchen von einer Adresse innerhalb von SPERRFENSTER ist diese
    Adresse SPERRDAUER lang gesperrt, nach FEHLVERSUCHE_GESAMT von beliebigen Adressen jede.
    Liegt nur im Speicher; ein Neustart des Dienstes hebt eine Sperre auf.
    """

    ALLE = "*"

    def __init__(self, uhr=None):
        self._uhr = uhr or _monoton
        self._lock = threading.Lock()
        self._fehler: dict[str, list[float]] = {}
        self._bis: dict[str, float] = {}

    def wartezeit(self, adresse: str) -> int:
        """Sekunden, bis von dieser Adresse wieder ein Versuch erlaubt ist (0: sofort)."""
        with self._lock:
            bis = max(self._bis.get(adresse, 0.0), self._bis.get(self.ALLE, 0.0))
        return max(0, math.ceil(bis - self._uhr()))

    def fehlschlag(self, adresse: str) -> int:
        """Merkt einen Fehlversuch; gibt die Wartezeit zurück, falls jetzt gesperrt ist."""
        jetzt = self._uhr()
        with self._lock:
            if len(self._fehler) > 1000:  # alte Einträge vergessen
                self._fehler = {k: v for k, v in self._fehler.items() if v and jetzt - v[-1] < SPERRFENSTER}
                self._bis = {k: v for k, v in self._bis.items() if v > jetzt}
            for schluessel, grenze in ((adresse, FEHLVERSUCHE), (self.ALLE, FEHLVERSUCHE_GESAMT)):
                versuche = [t for t in self._fehler.get(schluessel, []) if jetzt - t < SPERRFENSTER] + [jetzt]
                if len(versuche) >= grenze:
                    self._bis[schluessel] = jetzt + SPERRDAUER
                    versuche = []
                self._fehler[schluessel] = versuche
        return self.wartezeit(adresse)

    def erfolg(self, adresse: str) -> None:
        with self._lock:
            self._fehler.pop(adresse, None)


def _schluessel(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


@lru_cache(maxsize=8)
def _stand(benutzer: str, h: str | None, klartext: str | None, salz: str) -> str:
    """Fingerabdruck der Zugangsdaten; ändern sie sich, sind alle Sitzungen ungültig.

    Ein Passwort aus der Umgebung wird dafür wie ein gespeichertes gehasht (einmal je Prozess),
    damit in der Datenbank nichts steht, was sich leichter knacken ließe.
    """
    geheim = h or (passwort_hash(klartext, base64.b64decode(salz)) if klartext else "")
    return hashlib.sha256(f"{benutzer}:{geheim}".encode()).hexdigest()


class Anmeldung:
    """Anmeldung an der Weboberfläche mit dem gespeicherten Hash oder APP_BENUTZER/APP_PASSWORT.

    Nach der Anmeldung trägt der Browser ein zufälliges Sitzungs-Cookie; in der Datenbank steht nur
    dessen SHA-256. Eine Sitzung endet nach SITZUNG_LEERLAUF ohne Nutzung, spätestens nach
    SITZUNG_HOECHSTENS, beim Abmelden und sobald sich Benutzer oder Passwort ändern (auch über
    die Kommandozeile oder die Env-Datei).
    """

    def __init__(self, cfg: Config, db: Datenbank, uhr=None):
        self.cfg, self.db, self._uhr = cfg, db, uhr or _jetzt
        self.sperre = Sperre()
        if not db.einstellungen().get(SITZUNG_SALZ):
            db.einstellung_setzen(SITZUNG_SALZ, base64.b64encode(secrets.token_bytes(16)).decode())

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
        name_stimmt = hmac.compare_digest(benutzer.encode(), erwartet.encode())
        # Das Passwort auch bei falschem Namen prüfen, damit die Antwortzeit den Namen nicht verrät
        if h:
            passwort_ok = passwort_stimmt(passwort, h)
        else:
            passwort_ok = hmac.compare_digest(passwort.encode(), (klartext or "").encode())
        return name_stimmt and passwort_ok

    def pruefe_gebremst(self, benutzer: str, passwort: str, adresse: str) -> bool:
        """Wie pruefe, aber mit Sperre nach zu vielen Fehlversuchen (wirft Gesperrt)."""
        if warten := self.sperre.wartezeit(adresse):
            raise Gesperrt(warten)
        if self.pruefe(benutzer, passwort):
            self.sperre.erfolg(adresse)
            return True
        log.warning("Anmeldung als %r von %s fehlgeschlagen", benutzer, adresse)
        if warten := self.sperre.fehlschlag(adresse):
            log.warning("Anmeldung von %s für %d Sekunden gesperrt", adresse, warten)
            raise Gesperrt(warten)
        return False

    def _stand(self) -> str:
        return _stand(*self.zugang(), self.db.einstellungen()[SITZUNG_SALZ])

    def sitzung_anlegen(self) -> str:
        """Legt eine Sitzung an und gibt den Wert für das Cookie zurück."""
        jetzt = self._uhr()
        self.db.sitzungen_loeschen(erstellt_vor=jetzt - SITZUNG_HOECHSTENS, zuletzt_vor=jetzt - SITZUNG_LEERLAUF)
        wert = secrets.token_urlsafe(32)
        self.db.sitzung_anlegen(_schluessel(wert), self.zugang()[0], self._stand(), jetzt)
        return wert

    def sitzung_gueltig(self, wert: str | None) -> bool:
        if not self.aktiv:
            return True
        if not wert:
            return False
        s = self.db.sitzung(_schluessel(wert))
        jetzt = self._uhr()
        if (not s or not hmac.compare_digest(s["stand"], self._stand())
                or jetzt - s["zuletzt"] > SITZUNG_LEERLAUF or jetzt - s["erstellt"] > SITZUNG_HOECHSTENS):
            return False
        if jetzt - s["zuletzt"] > 60:
            self.db.sitzung_beruehren(s["schluessel"], jetzt)
        return True

    def abmelden(self, wert: str | None) -> None:
        if wert:
            self.db.sitzungen_loeschen(_schluessel(wert))

    def setzen(self, benutzer: str, passwort: str) -> None:
        if len(passwort) < MIN_PASSWORT:
            raise ValueError(f"Das Passwort muss mindestens {MIN_PASSWORT} Zeichen haben.")
        benutzer = benutzer.strip() or "admin"
        if ":" in benutzer:
            raise ValueError("Der Benutzername darf keinen Doppelpunkt enthalten.")
        self.db.einstellung_setzen(BENUTZER, benutzer)
        self.db.einstellung_setzen(PASSWORT_HASH, passwort_hash(passwort))
        self.db.sitzungen_loeschen()

    def zuruecksetzen(self) -> None:
        """Entfernt Benutzer und Passwort aus der Datenbank; dann gilt wieder die Umgebung."""
        self.db.einstellung_setzen(BENUTZER, None)
        self.db.einstellung_setzen(PASSWORT_HASH, None)
        self.db.sitzungen_loeschen()
