"""Geführter Monatsabschluss: Stand der einzelnen Schritte und Ablage von Kontoauszug und ELSTER-Protokoll.

Der Abschluss ist ein Angebot in der Oberfläche, kein Pflichtablauf. Was sich prüfen lässt, liest
`stand` aus den vorhandenen Daten (Kontoumsätze, Belege, Paperless, Stundenzettel), damit ein Schritt
als erledigt gilt, wenn er wirklich erledigt ist, egal ob über den Abschluss oder die normalen Reiter.

Kontoauszug und Protokoll landen in Paperless mit dem Tag aus den Einstellungen und einem Titel, der den
Monat nennt ("Kontoauszug September 2026"). So findet sie der ZIP-Export und die Frist-Anzeige
(`monatsexport.kontoauszug_vorhanden` bzw. `protokoll_vorhanden`) wie jedes andere Dokument.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from decimal import Decimal

from . import bank, monatsexport
from .berechnung import Bericht
from .config import Config
from .db import Datenbank
from .fristen import MONATSNAMEN, monat_plus
from .models import Umsatz
from .quellen.paperless import PaperlessClient
from .sevdesk import PaperlessAblage

MAIL_EINSTELLUNG = "monatsabschluss_mail"
MAIL_STANDARD = {
    "an": "",
    "betreff": "Unterlagen {monat} {jahr}",
    "text": "Hallo,\n\nanbei schicke ich dir die Unterlagen für {monat}.\n\nViele Grüße",
}
RE_MAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
MAX_PDF = 30_000_000


def _ablage_arten(cfg: Config) -> dict[str, tuple[str, str]]:
    """art -> (Titel-Anfang, Paperless-Tag)"""
    return {
        "kontoauszug": ("Kontoauszug", cfg.paperless_tag_kontoauszug),
        "protokoll": ("ELSTER-Protokoll UStVA", cfg.paperless_tag_protokoll),
    }


def mail_laden(db: Datenbank) -> dict:
    roh = db.einstellungen().get(MAIL_EINSTELLUNG)
    gespeichert = json.loads(roh) if roh else {}
    return {k: str(gespeichert.get(k, v)) for k, v in MAIL_STANDARD.items()}


def mail_speichern(db: Datenbank, eingabe: dict) -> dict:
    vorlage = {k: str(eingabe.get(k, v) or "").strip() for k, v in MAIL_STANDARD.items()}
    if vorlage["an"] and not all(RE_MAIL.match(a.strip()) for a in vorlage["an"].split(",")):
        raise ValueError(f"Keine gültige E-Mail-Adresse: {vorlage['an']}")
    if len(vorlage["betreff"]) > 200 or len(vorlage["text"]) > 5000:
        raise ValueError("Betreff oder Text ist zu lang.")
    db.einstellung_setzen(MAIL_EINSTELLUNG, json.dumps(vorlage, ensure_ascii=False))
    return vorlage


def stand(db: Datenbank, bericht: Bericht, umsaetze: list[Umsatz], pl: PaperlessClient | None,
          cfg: Config, protokoll: bool | None = None) -> dict:
    """Was im Monat schon da ist. `umsaetze` sind alle Kontoumsätze, `protokoll` kann vorab ermittelt sein."""
    zr = bericht.zeitraum
    jahr, monat = int(zr[:4]), int(zr[5:])
    im_monat = [u for u in umsaetze if u.zeitraum == zr]
    ergebnis: dict = {
        "zeitraum": zr,
        "monat": f"{MONATSNAMEN[monat - 1]} {jahr}",
        "umsaetze": len(im_monat),
        "letzte_buchung": max((u.buchungsdatum for u in im_monat), default=None),
        "ohne_beleg": len(bank.ohne_beleg(umsaetze, zr)),
        "belege": len(bericht.zeilen),
        "pruefen": sum(1 for z in bericht.zeilen if z.beleg.pruefen),
        "fehlerhaft": sum(1 for z in bericht.zeilen if z.fehler),
        "kontoauszug": None,
        "protokoll": protokoll,
        "paperless": pl is not None,
        "paperless_fehler": "",
        "stundenzettel": str(sum((h for d, h in db.stundenzettel(jahr) if d.month == monat), Decimal(0))),
        "mail": mail_laden(db),
    }
    if ergebnis["letzte_buchung"]:
        ergebnis["letzte_buchung"] = ergebnis["letzte_buchung"].isoformat()
    if pl is not None:
        try:
            if cfg.paperless_tag_kontoauszug:
                ergebnis["kontoauszug"] = monatsexport.kontoauszug_vorhanden(
                    pl, cfg.paperless_tag_kontoauszug, jahr, monat)
            if protokoll is None and cfg.paperless_tag_protokoll:
                ergebnis["protokoll"] = monatsexport.protokoll_vorhanden(pl, cfg.paperless_tag_protokoll, jahr, monat)
        except Exception as e:  # Paperless soll den Abschluss nicht blockieren
            ergebnis["paperless_fehler"] = str(e)
    return ergebnis


def ablegen(pl: PaperlessClient, cfg: Config, art: str, jahr: int, monat: int, dateiname: str, inhalt: bytes,
            heute: date, warten: float = 20.0, pause: float = 1.0) -> dict:
    """Lädt Kontoauszug oder ELSTER-Protokoll des Monats nach Paperless.

    Wartet bis zu `warten` Sekunden auf die Verarbeitung. Liegt dieselbe Datei schon in Paperless,
    bekommt das vorhandene Dokument den Tag.
    """
    arten = _ablage_arten(cfg)
    if art not in arten:
        raise ValueError(f"Unbekannte Ablage: {art}")
    if not inhalt.startswith(b"%PDF"):
        raise ValueError("Bitte eine PDF-Datei wählen.")
    if len(inhalt) > MAX_PDF:
        raise ValueError("Datei zu groß")
    titel_anfang, tag = arten[art]
    if not tag:
        raise ValueError(f"In den Einstellungen ist kein Paperless-Tag für „{titel_anfang}“ eingetragen.")
    titel = f"{titel_anfang} {MONATSNAMEN[monat - 1]} {jahr}"
    if art == "kontoauszug":
        nj, nm = monat_plus(jahr, monat, 1)
        erstellt = date(nj, nm, 1) - timedelta(days=1)  # Auszug zum Monatsende
    else:
        erstellt = heute  # Tag der Übermittlung
    ablage = PaperlessAblage(pl, cfg)
    aufgabe = ablage.dokument_hochladen(dateiname or f"{titel}.pdf", inhalt, titel, [tag], erstellt)
    ende = time.monotonic() + warten
    while True:
        zustand, wert = ablage.aufgabe(aufgabe)
        if zustand != "wartet" or time.monotonic() >= ende:
            break
        time.sleep(pause)
    if zustand == "doppelt":
        pl.senden("POST", "/api/documents/bulk_edit/",
                  {"documents": [int(wert)], "method": "add_tag", "parameters": {"tag": ablage.tag_id(tag)}})
    return {"zustand": zustand, "dokument": wert if zustand in ("fertig", "doppelt") else "",
            "fehler": wert if zustand == "fehler" else "", "titel": titel, "tag": tag}
