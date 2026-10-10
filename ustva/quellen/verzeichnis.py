"""Belege aus einem Verzeichnis einlesen.

Erwartete Struktur (Groß-/Kleinschreibung egal, Unterordner beliebig tief)::

    rechnungen/
      ausgang/   eigene Rechnungen an Kunden
      eingang/   Rechnungen von Lieferanten

Unterstützt werden PDF-Dateien mit Textebene und .txt-Dateien. Liegt neben einer
Datei eine gleichnamige ``.json``-Datei (``rechnung.pdf.json``), überschreiben
ihre Angaben die automatische Erkennung, z. B.::

    {"netto": "20.00", "waehrung": "USD", "steuerfall": "ausland_13b", "geprueft": true}
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from ..config import Config
from ..db import Datenbank
from ..extraktion import erkennen
from ..models import Beleg
from ..steuerfaelle import Art, Steuerfall
from . import ImportErgebnis, beleg_aus_erkennung

log = logging.getLogger(__name__)

ENDUNGEN = {".pdf", ".txt"}
DEZIMAL_FELDER = {"netto", "steuer", "steuersatz", "kurs"}
DATUM_FELDER = {"rechnungsdatum", "zahlungsdatum"}
TEXT_FELDER = {"waehrung", "partner", "partner_ustid", "rechnungsnummer", "beschreibung"}


def text_lesen(pfad: Path) -> str:
    if pfad.suffix.lower() == ".txt":
        return pfad.read_text(encoding="utf-8", errors="replace")
    leser = PdfReader(pfad)
    return "\n".join((seite.extract_text() or "") for seite in leser.pages)


def erste_zeile(text: str) -> str:
    """Bei Eingangsrechnungen steht der Absender meist in der ersten Zeile."""
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if zeile:
            return zeile if len(zeile) <= 60 else ""
    return ""


def art_aus_pfad(relativ: Path, cfg: Config) -> Art | None:
    teile = [t.lower() for t in relativ.parts[:-1]]
    if any(t in [o.lower() for o in cfg.ordner_ausgang] for t in teile):
        return Art.AUSGANG
    if any(t in [o.lower() for o in cfg.ordner_eingang] for t in teile):
        return Art.EINGANG
    return None


def zusatzangaben_anwenden(b: Beleg, daten: dict) -> None:
    for schluessel, wert in daten.items():
        if schluessel in DEZIMAL_FELDER:
            setattr(b, schluessel, Decimal(str(wert)) if wert not in (None, "") else None)
        elif schluessel in DATUM_FELDER:
            setattr(b, schluessel, date.fromisoformat(wert) if wert else None)
        elif schluessel in TEXT_FELDER:
            setattr(b, schluessel, str(wert))
        elif schluessel == "art":
            b.art = Art(wert)
        elif schluessel == "steuerfall":
            b.steuerfall = Steuerfall(wert)
        elif schluessel == "geprueft":
            b.pruefen = not bool(wert)
    if b.netto is None:
        b.netto = Decimal(0)


def importieren(db: Datenbank, cfg: Config, verzeichnis: Path | None = None) -> ImportErgebnis:
    verzeichnis = verzeichnis or cfg.rechnungs_verzeichnis
    ergebnis = ImportErgebnis()
    if not verzeichnis or not verzeichnis.is_dir():
        ergebnis.fehler.append(f"Verzeichnis nicht gefunden: {verzeichnis}")
        return ergebnis

    for pfad in sorted(verzeichnis.rglob("*")):
        if not pfad.is_file() or pfad.suffix.lower() not in ENDUNGEN:
            continue
        relativ = pfad.relative_to(verzeichnis)
        art = art_aus_pfad(relativ, cfg)
        if art is None:
            ergebnis.uebersprungen += 1
            continue
        pruefsumme = hashlib.sha256(pfad.read_bytes()).hexdigest()
        if db.beleg_vorhanden("verzeichnis", pruefsumme):
            ergebnis.vorhanden += 1
            continue
        try:
            text = text_lesen(pfad)
            erkennung = erkennen(text, art, cfg.eigene_ustid)
            beleg = beleg_aus_erkennung(
                erkennung, art,
                quelle="verzeichnis", quelle_ref=pruefsumme, quelle_link=relativ.as_posix(),
                beschreibung=pfad.name,
            )
            if art == Art.EINGANG:
                beleg.partner = erste_zeile(text)
            zusatz = pfad.with_name(pfad.name + ".json")
            if zusatz.is_file():
                zusatzangaben_anwenden(beleg, json.loads(zusatz.read_text(encoding="utf-8")))
            db.beleg_anlegen(beleg)
            ergebnis.neu += 1
        except Exception as e:  # eine kaputte Datei soll den Import nicht abbrechen
            log.exception("Fehler beim Einlesen von %s", pfad)
            ergebnis.fehler.append(f"{relativ}: {e}")
    return ergebnis
