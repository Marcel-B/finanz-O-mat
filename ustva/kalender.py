"""Urlaub und andere freie Tage aus einem Kalender (ICS-Adresse, z. B. aus Nextcloud).

Es zählen nur ganztägige Termine: Urlaub, Brückentage oder Krankheit trägt man so ein, Besprechungen
haben eine Uhrzeit und bleiben außen vor. Wiederkehrende Termine (Geburtstage, Serien) zählen nicht.
Mit Stichwörtern (durch Komma getrennt) zählen nur Termine, deren Titel eines davon enthält, z. B.
„Urlaub, Krank“; so lässt sich auch der normale Kalender verknüpfen.

Nextcloud: im Kalender „Teilen“ → „Link teilen“ und den Link eintragen, die App macht daraus die
Export-Adresse. Ein nicht geteilter Kalender geht mit App-Passwort in der Adresse:
``https://benutzer:app-passwort@cloud.example.de/remote.php/dav/calendars/benutzer/urlaub?export``.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx

CACHE_SEKUNDEN = 15 * 60  # Urlaub ändert sich selten; neu geladen wird spätestens nach 15 Minuten
FEHLER_SEKUNDEN = 2 * 60  # nach einem Fehler nicht bei jedem Aufruf erneut warten
RE_DAUER = re.compile(r"^P(?:(\d+)W)?(?:(\d+)D)?")
RE_NEXTCLOUD_LINK = re.compile(r"^(.*?)/(?:index\.php/)?apps/calendar/p/([A-Za-z0-9]+)(?:/.*)?$")


@dataclass(frozen=True)
class Termin:
    titel: str
    beginn: date
    ende: date  # erster Tag danach, wie DTEND bei ganztägigen Terminen

    def tage(self) -> list[date]:
        tag, tage = self.beginn, []
        while tag < self.ende:
            tage.append(tag)
            tag += timedelta(days=1)
        return tage


class KalenderFehler(Exception):
    pass


def export_adresse(url: str) -> str:
    """Macht aus einem geteilten Nextcloud-Link oder einer webcal-Adresse die Adresse der ICS-Datei."""
    url = url.strip()
    url = re.sub(r"^webcal(s?)://", "https://", url, flags=re.IGNORECASE)
    if m := RE_NEXTCLOUD_LINK.match(url.split("?")[0]):
        return f"{m.group(1)}/remote.php/dav/public-calendars/{m.group(2)}?export"
    teile = urlsplit(url)
    if "/remote.php/dav/" in teile.path and "export" not in teile.query.split("&"):
        query = f"{teile.query}&export" if teile.query else "export"
        return urlunsplit(teile._replace(query=query))
    return url


def anzeige(url: str) -> str:
    """Adresse ohne Zugangsdaten und Freigabe-Token, um zu zeigen, welcher Kalender verknüpft ist."""
    teile = urlsplit(export_adresse(url))
    return teile.hostname or ""


def _zeilen(text: str) -> list[str]:
    """Entfaltet umbrochene Zeilen (RFC 5545: Folgezeile beginnt mit Leerzeichen oder Tab)."""
    zeilen: list[str] = []
    for zeile in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if zeile[:1] in (" ", "\t") and zeilen:
            zeilen[-1] += zeile[1:]
        elif zeile:
            zeilen.append(zeile)
    return zeilen


def _text(wert: str) -> str:
    return (wert.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";")
            .replace("\\\\", "\\").strip())


def _datum(wert: str) -> date | None:
    """Nur reine Datumswerte (ganztägig); Werte mit Uhrzeit liefern None."""
    if len(wert) != 8 or not wert.isdigit():
        return None
    try:
        return date(int(wert[:4]), int(wert[4:6]), int(wert[6:]))
    except ValueError:
        return None


def termine(ics: str) -> list[Termin]:
    """Ganztägige, nicht wiederkehrende und nicht abgesagte Termine einer ICS-Datei."""
    ergebnis: list[Termin] = []
    felder: dict[str, str] | None = None
    tiefe = 0  # VALARM u. Ä. innerhalb eines Termins überspringen
    for zeile in _zeilen(ics):
        name, _, wert = zeile.partition(":")
        name = name.split(";")[0].upper()
        if name == "BEGIN":
            if wert.strip().upper() == "VEVENT" and felder is None:
                felder, tiefe = {}, 0
            elif felder is not None:
                tiefe += 1
            continue
        if name == "END" and felder is not None:
            if tiefe:
                tiefe -= 1
                continue
            if termin := _termin(felder):
                ergebnis.append(termin)
            felder = None
            continue
        if felder is not None and not tiefe:
            felder.setdefault(name, wert.strip())
    return ergebnis


def _termin(f: dict[str, str]) -> Termin | None:
    if "RRULE" in f or "RECURRENCE-ID" in f or f.get("STATUS", "").upper() == "CANCELLED":
        return None
    beginn = _datum(f.get("DTSTART", ""))
    if beginn is None:
        return None
    ende = _datum(f.get("DTEND", ""))
    if ende is None and (m := RE_DAUER.match(f.get("DURATION", ""))) and any(m.groups()):
        ende = beginn + timedelta(weeks=int(m.group(1) or 0), days=int(m.group(2) or 0))
    if ende is None or ende <= beginn:
        ende = beginn + timedelta(days=1)
    return Termin(_text(f.get("SUMMARY", "")) or "Urlaub", beginn, ende)


def stichwoerter(text: str) -> list[str]:
    return [s.strip().lower() for s in text.split(",") if s.strip()]


def freie_tage(liste: list[Termin], stichwort: str = "") -> dict[date, str]:
    """Tag → Titel des Termins; mit Stichwörtern nur passende Termine."""
    worte = stichwoerter(stichwort)
    tage: dict[date, str] = {}
    for t in sorted(liste, key=lambda t: t.beginn):
        if worte and not any(w in t.titel.lower() for w in worte):
            continue
        for tag in t.tage():
            tage.setdefault(tag, t.titel)
    return tage


class Kalender:
    """Lädt den Kalender höchstens alle 15 Minuten; ist er nicht erreichbar, gilt der letzte Stand."""

    def __init__(self, client: Callable[[], httpx.Client] | None = None, uhr: Callable[[], float] = time.monotonic):
        self._client = client or (lambda: httpx.Client(timeout=10, follow_redirects=True))
        self._uhr = uhr
        self._sperre = threading.Lock()
        self._stand: dict[str, tuple[float, list[Termin], str]] = {}  # geladen um, Termine, Fehler

    def laden(self, url: str) -> list[Termin]:
        """Liest den Kalender sofort (und merkt ihn sich); wirft KalenderFehler mit einer verständlichen Meldung."""
        adresse = export_adresse(url)
        try:
            with self._client() as client:
                antwort = client.get(adresse, headers={"Accept": "text/calendar"})
        except httpx.HTTPError as e:
            raise KalenderFehler(f"Kalender nicht erreichbar ({anzeige(url)}): {e.__class__.__name__}") from e
        if antwort.status_code in (401, 403):
            raise KalenderFehler("Der Kalender verlangt eine Anmeldung. Link teilen oder App-Passwort angeben.")
        if antwort.status_code == 404:
            raise KalenderFehler("Kalender nicht gefunden, bitte die Adresse prüfen.")
        if antwort.status_code >= 400:
            raise KalenderFehler(f"Der Kalender antwortet mit Fehler {antwort.status_code}.")
        if "BEGIN:VCALENDAR" not in antwort.text[:2000].upper():
            raise KalenderFehler("Unter der Adresse liegt kein Kalender (ICS), bitte den Teilen-Link nehmen.")
        liste = termine(antwort.text)
        with self._sperre:
            self._stand[url] = (self._uhr(), liste, "")
        return liste

    def tage(self, url: str, stichwort: str = "") -> tuple[dict[date, str], str]:
        """Freie Tage und ggf. eine Fehlermeldung; ohne Adresse nichts."""
        if not url:
            return {}, ""
        with self._sperre:
            stand = self._stand.get(url)
        if stand is None or self._uhr() - stand[0] > (FEHLER_SEKUNDEN if stand[2] else CACHE_SEKUNDEN):
            try:
                self.laden(url)
            except KalenderFehler as e:
                with self._sperre:
                    self._stand[url] = (self._uhr(), stand[1] if stand else [], str(e))
            with self._sperre:
                stand = self._stand[url]
        return freie_tage(stand[1], stichwort), stand[2]
