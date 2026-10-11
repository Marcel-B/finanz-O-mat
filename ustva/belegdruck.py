"""Belege eines Monats drucken: alle Dateien als ein PDF, Druck am Beleg vermerken.

Es sind dieselben Belege wie im ZIP für die Steuerberatung (`monatsexport.monatsbelege`):
Ausgangsrechnungen zuerst, dann Eingangsrechnungen, jeweils nach Rechnungsdatum. Mehrere
Belege aus derselben Datei (ein Beleg je Steuersatz) kommen nur einmal ins PDF.
Was keine PDF-Datei hat (manuell angelegt, XML- oder Bilddatei im Verzeichnis), bleibt außen vor
und wird in der Liste mit Grund genannt. Als gedruckt vermerkt werden nur Belege, die im PDF stehen.

Rechnungen mit Kopierschutz (verschlüsselt, ohne Passwort zu öffnen) liest pypdf mit dem Zusatz
`crypto`. Ist die archivierte Fassung aus Paperless unbrauchbar, wird das Original versucht.
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


def gleiche_datei(b: Beleg, alle: list[Beleg]) -> list[int]:
    """IDs aller Belege, die zur Datei von `b` gehören (ein Beleg je Steuersatz), `b` eingeschlossen."""
    return [x.id for x in alle if x.id and _dokument(x) == _dokument(b)] if b.quelle in ("paperless", "verzeichnis") \
        else [b.id]


def grund(cfg: Config, b: Beleg) -> str:
    """Warum der Beleg nicht ins PDF kommt; leer, wenn er gedruckt werden kann."""
    if b.quelle == "paperless":
        return "" if cfg.paperless_aktiv else "Paperless ist nicht eingerichtet"
    if b.quelle == "verzeichnis":
        endung = PurePath(b.quelle_link).suffix.lower()
        return "" if endung == ".pdf" else f"{endung or 'Datei'} ohne PDF, bitte selbst drucken"
    return "keine Datei hinterlegt"


def liste(cfg: Config, belege: list[Beleg], fehler: dict[int, str] | None = None) -> list[dict]:
    """`fehler`: warum ein Beleg beim letzten Druck nicht ins PDF kam."""
    fehler = fehler or {}
    return [{"id": b.id, "name": beleg_dateiname(b, ""), "partner": b.partner or b.beschreibung,
             "art": b.art.value, "rechnungsdatum": b.rechnungsdatum.isoformat() if b.rechnungsdatum else None,
             "gedruckt": b.gedruckt, "grund": grund(cfg, b), "fehler": fehler.get(b.id, "")}
            for b in reihenfolge(belege)]


def _lesen(inhalt: bytes) -> PdfReader:
    start = inhalt.find(b"%PDF", 0, 1024)  # manche Erzeuger schreiben etwas vor den Kopf
    if start < 0:
        raise ValueError("Datei ist kein PDF")
    reader = PdfReader(io.BytesIO(inhalt[start:]))
    if not reader.pages:
        raise ValueError("PDF ohne Seiten")
    return reader


def _beleg_lesen(cfg: Config, b: Beleg, pl: PaperlessClient | None) -> PdfReader:
    _, inhalt = beleg_datei(cfg, b, pl)
    try:
        return _lesen(inhalt)
    except Exception:
        if b.quelle != "paperless" or pl is None:
            raise
        return _lesen(pl.original(int(b.quelle_ref.split("#")[0])))


def pdf(cfg: Config, belege: list[Beleg], pl: PaperlessClient | None) -> tuple[bytes, list[int], dict[int, str]]:
    """(PDF, IDs der enthaltenen Belege, Fehler je Beleg-ID)."""
    writer = PdfWriter()
    enthalten: list[int] = []
    fehler: dict[int, str] = {}
    dokumente: dict[tuple[str, str], str | None] = {}  # Datei -> Fehler, None = im PDF
    for b in reihenfolge([b for b in belege if not grund(cfg, b)]):
        schluessel = _dokument(b)
        if schluessel in dokumente:  # weiterer Beleg aus derselben Datei
            if dokumente[schluessel] is None:
                enthalten.append(b.id)
            else:
                fehler[b.id] = dokumente[schluessel]
            continue
        try:
            writer.append(_beleg_lesen(cfg, b, pl))
            dokumente[schluessel] = None
            enthalten.append(b.id)
        except Exception as e:  # ein kaputter Beleg soll den Druck der anderen nicht verhindern
            dokumente[schluessel] = fehler[b.id] = str(e) or type(e).__name__
    puffer = io.BytesIO()
    writer.write(puffer)
    return puffer.getvalue(), enthalten, fehler
