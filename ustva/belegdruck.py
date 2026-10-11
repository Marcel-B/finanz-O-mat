"""Belege eines Monats drucken: alle Dateien als ein PDF, Druck am Beleg vermerken.

Es sind dieselben Belege wie im ZIP für die Steuerberatung (`monatsexport.monatsbelege`):
Ausgangsrechnungen zuerst, dann Eingangsrechnungen, jeweils nach Rechnungsdatum. Mehrere
Belege aus derselben Datei (ein Beleg je Steuersatz) kommen nur einmal ins PDF.
Was keine PDF-Datei hat (manuell angelegt, XML- oder Bilddatei im Verzeichnis), bleibt außen vor
und wird in der Liste mit Grund genannt. Als gedruckt vermerkt werden nur Belege, die im PDF stehen.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import PurePath

from pypdf import PdfReader, PdfWriter

from .config import Config
from .models import Beleg
from .monatsexport import beleg_datei, beleg_dateiname
from .quellen.paperless import PaperlessClient


def reihenfolge(belege: list[Beleg]) -> list[Beleg]:
    return sorted(belege, key=lambda b: (b.art.value != "ausgang", b.rechnungsdatum or date.min, b.id or 0))


def _dokument(b: Beleg) -> tuple[str, str]:
    """Datei des Belegs; weitere Belege derselben Rechnung haben "#2", "#3" an der Referenz."""
    return b.quelle, b.quelle_ref.split("#")[0]


def grund(cfg: Config, b: Beleg) -> str:
    """Warum der Beleg nicht ins PDF kommt; leer, wenn er gedruckt werden kann."""
    if b.quelle == "paperless":
        return "" if cfg.paperless_aktiv else "Paperless ist nicht eingerichtet"
    if b.quelle == "verzeichnis":
        endung = PurePath(b.quelle_link).suffix.lower()
        return "" if endung == ".pdf" else f"{endung or 'Datei'} ohne PDF, bitte selbst drucken"
    return "keine Datei hinterlegt"


def liste(cfg: Config, belege: list[Beleg]) -> list[dict]:
    return [{"id": b.id, "name": beleg_dateiname(b, ""), "partner": b.partner or b.beschreibung,
             "art": b.art.value, "rechnungsdatum": b.rechnungsdatum.isoformat() if b.rechnungsdatum else None,
             "gedruckt": b.gedruckt, "grund": grund(cfg, b)} for b in reihenfolge(belege)]


def pdf(cfg: Config, belege: list[Beleg], pl: PaperlessClient | None) -> tuple[bytes, list[int], list[str]]:
    """(PDF, IDs der enthaltenen Belege, Fehler)."""
    writer = PdfWriter()
    enthalten: list[int] = []
    fehler: list[str] = []
    dokumente: dict[tuple[str, str], bool] = {}
    for b in reihenfolge([b for b in belege if not grund(cfg, b)]):
        schluessel = _dokument(b)
        if schluessel in dokumente:  # weiterer Beleg aus derselben Datei
            if dokumente[schluessel]:
                enthalten.append(b.id)
            continue
        try:
            _, inhalt = beleg_datei(cfg, b, pl)
            if not inhalt.startswith(b"%PDF"):
                raise ValueError("Datei ist kein PDF")
            writer.append(PdfReader(io.BytesIO(inhalt)))
            dokumente[schluessel] = True
            enthalten.append(b.id)
        except Exception as e:  # ein kaputter Beleg soll den Druck der anderen nicht verhindern
            dokumente[schluessel] = False
            fehler.append(f"{beleg_dateiname(b, '')}: {e}")
    puffer = io.BytesIO()
    writer.write(puffer)
    return puffer.getvalue(), enthalten, fehler
