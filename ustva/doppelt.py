"""Erkennt Belege, die dieselbe Rechnung zweimal erfassen.

Typischer Fall: Die Rechnung liegt zweimal in Paperless, etwa aus der Mail und aus
der sevDesk-Übernahme. Die Dateien unterscheiden sich, der Inhalt ist derselbe.
Verglichen wird deshalb, was auf der Rechnung steht: Art, Datum, Betrag und die
Rechnungsnummer, ohne Nummer der Partner.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Beleg


def _norm(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def gleich(a: Beleg, b: Beleg) -> bool:
    """Ist b wohl dieselbe Rechnung wie a?"""
    if (a.art, a.rechnungsdatum, a.waehrung) != (b.art, b.rechnungsdatum, b.waehrung) or a.rechnungsdatum is None:
        return False
    if not a.zahlbetrag() or a.zahlbetrag() != b.zahlbetrag():
        return False
    na, nb = _norm(a.rechnungsnummer), _norm(b.rechnungsnummer)
    if na and nb:
        return na == nb
    # "Telekom" und "Telekom Deutschland GmbH" sind derselbe Partner
    kurz, lang = sorted((_norm(a.partner), _norm(b.partner)), key=len)
    return len(kurz) >= 4 and lang.startswith(kurz)


def _dokument(b: Beleg) -> tuple[str, str] | None:
    """Herkunft ohne Postennummer; die Steuerposten einer E-Rechnung sind kein Duplikat voneinander."""
    return (b.quelle, b.quelle_ref.split("#")[0]) if b.quelle_ref else None


def doppel(beleg: Beleg, vorhandene: Iterable[Beleg]) -> Beleg | None:
    """Der erste vorhandene Beleg, der dieselbe Rechnung ist, sonst None."""
    dok = _dokument(beleg)
    return next((b for b in vorhandene if b.id != beleg.id and (dok is None or _dokument(b) != dok)
                 and gleich(b, beleg)), None)


def herkunft(b: Beleg) -> str:
    if b.quelle == "paperless":
        return f"Paperless-Dokument {b.quelle_ref.split('#')[0]}"
    if b.quelle == "verzeichnis":
        return f"Datei {b.quelle_link}"
    return "manuell angelegt"


def beschreibung(b: Beleg) -> str:
    datum = f"{b.rechnungsdatum:%d.%m.%Y}" if b.rechnungsdatum else "ohne Datum"
    betrag = f"{b.zahlbetrag():.2f}".replace(".", ",")
    nummer = f", Nr. {b.rechnungsnummer}" if b.rechnungsnummer else ""
    return f"{b.partner or b.beschreibung or 'Beleg'} vom {datum} über {betrag} {b.waehrung}{nummer}"
