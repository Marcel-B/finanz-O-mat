"""sevDesk-Umzug in der Oberfläche: Export-Ordner hochladen, Probelauf ansehen, übernehmen.

Der Browser schickt die Dateien einzeln (ganzer Ordner per Drag & Drop oder die ZIPs aus sevDesk). Sie landen in
einem Ordner neben der Datenbank und bleiben dort als Archiv liegen. Die Übernahme läuft im Hintergrund, weil
Paperless jede Datei erst verarbeiten muss.
"""

from __future__ import annotations

import io
import shutil
import threading
import zipfile
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import adressbuch, sevdesk
from .config import Config
from .db import Datenbank
from .sevdesk_export import Export
from .waehrung import Umrechner

CSV_NAMEN = {"voucher.csv", "invoices.csv", "contacts.csv"}
ENDUNGEN = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".webp", ".xml"}
ZIP_HOECHSTENS = 4 * 1024**3  # entpackt; schützt vor ZIP-Bomben


class ImportOptionen(BaseModel):
    von: str | None = None
    bis: str | None = None


def _ziel(ordner: Path, name: str) -> Path | None:
    """Wohin eine hochgeladene Datei gehört; None für Dateien, die das Werkzeug nicht braucht."""
    datei = PurePosixPath(name.replace("\\", "/")).name
    if not datei or datei.startswith("."):
        return None
    if datei.lower() in CSV_NAMEN:
        return ordner / datei.lower()
    if Path(datei).suffix.lower() in ENDUNGEN:
        return ordner / "dateien" / datei
    return None


def speichern(ordner: Path, name: str, inhalt: bytes) -> int:
    """Legt eine Datei ab bzw. entpackt ein ZIP; Anzahl der abgelegten Dateien."""
    if name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(inhalt)) as z:
                eintraege = [e for e in z.infolist() if not e.is_dir()]
                if sum(e.file_size for e in eintraege) > ZIP_HOECHSTENS:
                    raise HTTPException(413, f"{name}: entpackt zu groß")
                return sum(speichern(ordner, e.filename, z.read(e)) for e in eintraege)
        except zipfile.BadZipFile as e:
            raise HTTPException(400, f"{name}: kein gültiges ZIP") from e
    ziel = _ziel(ordner, name)
    if ziel is None:
        return 0
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(inhalt)
    return 1


def _euro(wert: Decimal | None) -> str | None:
    return None if wert is None else str(wert.quantize(Decimal("0.01")))


def ergebnis(erg: sevdesk.Uebernahme, export: Export, db: Datenbank, umrechner: Umrechner, cfg: Config,
             trocken: bool, von: str | None, bis: str | None, kontakte: int) -> dict:
    erwartet, offen = export.abgegeben()
    belege = db.alle_belege() + (erg.belege if trocken else [])
    monate = sevdesk.monate(erg.belege, cfg.versteuerung, von, bis)
    vergleich = sevdesk.monatsvergleich(belege, umrechner, cfg.versteuerung, monate, erwartet)
    return {
        "trocken": trocken,
        "neu": erg.neu,
        "belege": len(erg.belege),
        "vorhanden": erg.vorhanden,
        "ausserhalb": erg.ausserhalb,
        "hochgeladen": erg.hochgeladen,
        "wartet": erg.wartet,
        "kontakte": kontakte,
        "uebersprungen": [{"grund": g, "anzahl": n} for g, n in sorted(erg.uebersprungen.items())],
        "doppelt": erg.doppelt,
        "pruefen": erg.pruefen,
        "ohne_datei": erg.ohne_datei,
        "fehler": erg.fehler,
        "zahlungen_ohne_zeitraum": offen,
        "vergleich": [
            {**z, **{k: _euro(z[k]) for k in ("zahllast", "umsatzsteuer", "vorsteuer", "erwartet", "differenz")}}
            for z in vergleich
        ],
    }


class Lauf:
    """Eine Übernahme im Hintergrund; es läuft immer höchstens eine."""

    def __init__(self):
        self.lock = threading.Lock()
        self.laeuft = False
        self.meldung = ""
        self.ergebnis: dict | None = None
        self.fehler = ""
        self.beendet = ""

    def starten(self, arbeit: Callable[[Callable[[str], None]], dict]) -> None:
        with self.lock:
            if self.laeuft:
                raise HTTPException(409, "Die Übernahme läuft schon")
            self.laeuft, self.meldung, self.ergebnis, self.fehler = True, "Startet …", None, ""

        def ausfuehren():
            try:
                e = arbeit(self._melden)
                with self.lock:
                    self.ergebnis = e
            except Exception as e:  # dem Browser zeigen statt im Thread zu verschwinden
                with self.lock:
                    self.fehler = str(e) or type(e).__name__
            finally:
                with self.lock:
                    self.laeuft, self.meldung = False, ""
                    self.beendet = datetime.now().isoformat(timespec="seconds")

        threading.Thread(target=ausfuehren, name="sevdesk-import", daemon=True).start()

    def _melden(self, text: str) -> None:
        with self.lock:
            self.meldung = text

    def status(self) -> dict:
        with self.lock:
            return {"laeuft": self.laeuft, "meldung": self.meldung, "ergebnis": self.ergebnis, "fehler": self.fehler,
                    "beendet": self.beendet}


def router(ordner: Path, db: Datenbank, aktuell: Callable[[], Config], umrechner: Umrechner,
           warten: float = 300) -> APIRouter:
    r = APIRouter(prefix="/api/sevdesk")
    lauf = Lauf()

    def _export() -> Export:
        if not Export.ist_export(ordner):
            raise HTTPException(400, "Noch keine sevDesk-Export-Dateien hochgeladen (voucher.csv bzw. invoices.csv)")
        return Export.laden(ordner)

    @r.get("")
    def status():
        dateien = list((ordner / "dateien").glob("*")) if (ordner / "dateien").exists() else []
        return {
            "csv": sorted(n for n in CSV_NAMEN if (ordner / n).exists()),
            "dateien": len(dateien),
            "paperless": aktuell().paperless_aktiv,
            **lauf.status(),
        }

    @r.delete("/dateien", status_code=204)
    def leeren():
        if lauf.status()["laeuft"]:
            raise HTTPException(409, "Die Übernahme läuft gerade")
        if ordner.exists():
            shutil.rmtree(ordner)

    @r.put("/dateien")
    async def hochladen(request: Request, name: str):
        if lauf.status()["laeuft"]:
            raise HTTPException(409, "Die Übernahme läuft gerade")
        return {"abgelegt": speichern(ordner, name, await request.body())}

    @r.post("/probelauf")
    def probelauf(optionen: ImportOptionen):
        export = _export()
        cfg = aktuell()
        erg = sevdesk.importieren(db, cfg, export, trocken=True, von=optionen.von, bis=optionen.bis)
        return ergebnis(erg, export, db, umrechner, cfg, True, optionen.von, optionen.bis, len(export.adressbuch()))

    @r.post("/importieren", status_code=202)
    def importieren(optionen: ImportOptionen):
        export = _export()
        cfg = aktuell()

        def arbeit(melden: Callable[[str], None]) -> dict:
            erg = sevdesk.importieren(db, cfg, export, paperless_nutzen=cfg.paperless_aktiv, von=optionen.von,
                                      bis=optionen.bis, warten=warten, fortschritt=melden)
            a = adressbuch.uebernehmen(db, export.adressbuch())
            return ergebnis(erg, export, db, umrechner, cfg, False, optionen.von, optionen.bis, a.neu + a.ergaenzt)

        lauf.starten(arbeit)
        return lauf.status()

    return r
