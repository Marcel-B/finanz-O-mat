"""Web-Oberfläche und JSON-API."""

from __future__ import annotations

import base64
import re
import secrets
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import export
from .berechnung import berechnen, zeitraum
from .config import Config
from .db import Datenbank
from .fristen import naechste_frist
from .models import Beleg
from .quellen import ImportErgebnis, paperless, verzeichnis
from .steuerfaelle import KENNZAHLEN, STEUERFAELLE, Art, Steuerfall
from .waehrung import Umrechner

STATIC = Path(__file__).parent / "static"
RE_ZEITRAUM = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def _zeitraum(wert: str) -> tuple[int, int]:
    m = RE_ZEITRAUM.match(wert)
    if not m:
        raise HTTPException(400, "Zeitraum im Format JJJJ-MM angeben")
    return int(m.group(1)), int(m.group(2))


class BelegEingabe(BaseModel):
    art: Art
    steuerfall: Steuerfall
    rechnungsdatum: date | None = None
    zahlungsdatum: date | None = None
    netto: Decimal
    steuersatz: Decimal = Decimal(19)
    steuer: Decimal | None = None
    waehrung: str = "EUR"
    kurs: Decimal | None = None
    partner: str = ""
    partner_ustid: str = ""
    rechnungsnummer: str = ""
    beschreibung: str = ""
    pruefen: bool = False

    @field_validator("waehrung")
    @classmethod
    def _waehrung(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("Währung als dreistelligen ISO-Code angeben, z. B. EUR oder USD")
        return v

    @field_validator("kurs")
    @classmethod
    def _kurs(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError("Kurs muss größer als 0 sein")
        return v


class KursEingabe(BaseModel):
    waehrung: str
    zeitraum: str
    kurs: Decimal
    quelle: str = "BMF"


class ImportAnfrage(BaseModel):
    quelle: str = "alle"  # alle | verzeichnis | paperless
    zeitraum: str


def create_app(cfg: Config | None = None, db: Datenbank | None = None, umrechner: Umrechner | None = None,
               heute=None) -> FastAPI:
    cfg = cfg or Config.aus_umgebung()
    db = db or Datenbank(cfg.datenbank)
    umrechner = umrechner or Umrechner(db, cfg.ezb_kurse)
    app = FastAPI(title="Finanz-O-Mat", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def _heute() -> date:
        return heute() if callable(heute) else (heute or date.today())

    if cfg.app_benutzer and cfg.app_passwort:
        erwartet = base64.b64encode(f"{cfg.app_benutzer}:{cfg.app_passwort}".encode()).decode()

        @app.middleware("http")
        async def anmeldung(request: Request, call_next):
            kopf = request.headers.get("authorization", "")
            if not secrets.compare_digest(kopf.encode(), f"Basic {erwartet}".encode()):
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Finanz-O-Mat"'})
            return await call_next(request)

    @app.get("/api/status")
    def status():
        frist = naechste_frist(_heute(), cfg.bundesland, cfg.dauerfristverlaengerung)
        return {
            "frist": frist.to_dict(),
            "versteuerung": cfg.versteuerung,
            "dauerfristverlaengerung": cfg.dauerfristverlaengerung,
            "bundesland": cfg.bundesland,
            "verzeichnis": str(cfg.rechnungs_verzeichnis) if cfg.rechnungs_verzeichnis else None,
            "paperless": cfg.paperless_url if cfg.paperless_aktiv else None,
        }

    @app.get("/api/steuerfaelle")
    def steuerfaelle():
        return [
            {"wert": f.value, "titel": i.titel, "art": i.art.value if i.art else None,
             "kennzahlen": i.kennzahlen, "sonderfall": i.sonderfall}
            for f, i in STEUERFAELLE.items()
        ]

    @app.get("/api/kennzahlen")
    def kennzahlen():
        return {k: {"titel": v.titel, "betrag_art": v.betrag_art} for k, v in KENNZAHLEN.items()}

    @app.get("/api/bericht/{zr}")
    def bericht(zr: str):
        jahr, monat = _zeitraum(zr)
        return berechnen(db.alle_belege(), jahr, monat, umrechner, cfg.versteuerung).to_dict()

    @app.get("/api/offen")
    def offen():
        """Belege ohne Zeitraum: kein Rechnungsdatum oder (Ist-Versteuerung) noch nicht bezahlt."""
        return [b.to_dict() for b in db.alle_belege() if zeitraum(b, cfg.versteuerung) is None]

    @app.post("/api/belege", status_code=201)
    def beleg_anlegen(eingabe: BelegEingabe):
        return db.beleg_anlegen(Beleg(**eingabe.model_dump())).to_dict()

    @app.put("/api/belege/{beleg_id}")
    def beleg_aendern(beleg_id: int, eingabe: BelegEingabe):
        alt = _beleg(beleg_id)
        neu = Beleg(**eingabe.model_dump(), quelle=alt.quelle, quelle_ref=alt.quelle_ref,
                    quelle_link=alt.quelle_link, hinweise=alt.hinweise, id=beleg_id)
        return db.beleg_aktualisieren(neu).to_dict()

    @app.post("/api/belege/{beleg_id}/geprueft")
    def beleg_geprueft(beleg_id: int):
        b = _beleg(beleg_id)
        b.pruefen = False
        return db.beleg_aktualisieren(b).to_dict()

    @app.delete("/api/belege/{beleg_id}", status_code=204)
    def beleg_loeschen(beleg_id: int):
        _beleg(beleg_id)
        db.beleg_loeschen(beleg_id)
        return Response(status_code=204)

    @app.get("/api/belege/{beleg_id}/datei")
    def beleg_datei(beleg_id: int):
        b = _beleg(beleg_id)
        if b.quelle != "verzeichnis" or not cfg.rechnungs_verzeichnis:
            raise HTTPException(404, "Keine Datei zu diesem Beleg")
        basis = cfg.rechnungs_verzeichnis.resolve()
        pfad = (basis / b.quelle_link).resolve()
        if not pfad.is_relative_to(basis) or not pfad.is_file():
            raise HTTPException(404, "Datei nicht gefunden")
        return FileResponse(pfad)

    def _beleg(beleg_id: int) -> Beleg:
        b = db.beleg(beleg_id)
        if b is None:
            raise HTTPException(404, "Beleg nicht gefunden")
        return b

    @app.post("/api/import")
    def importieren(anfrage: ImportAnfrage):
        jahr, monat = _zeitraum(anfrage.zeitraum)
        ergebnisse: dict[str, dict] = {}
        if anfrage.quelle in ("alle", "verzeichnis") and cfg.rechnungs_verzeichnis:
            ergebnisse["verzeichnis"] = verzeichnis.importieren(db, cfg).to_dict()
        if anfrage.quelle in ("alle", "paperless") and cfg.paperless_aktiv:
            ergebnisse["paperless"] = paperless.importieren(db, cfg, jahr, monat).to_dict()
        if not ergebnisse:
            leer = ImportErgebnis(fehler=["Keine Quelle konfiguriert (RECHNUNGS_VERZEICHNIS oder PAPERLESS_URL)"])
            ergebnisse["keine"] = leer.to_dict()
        return ergebnisse

    @app.get("/api/kurse")
    def kurse():
        return db.alle_kurse()

    @app.put("/api/kurse")
    def kurs_speichern(eingabe: KursEingabe):
        _zeitraum(eingabe.zeitraum)
        if eingabe.kurs <= 0:
            raise HTTPException(400, "Kurs muss größer als 0 sein")
        db.kurs_speichern(eingabe.waehrung.strip().upper(), eingabe.zeitraum, eingabe.kurs, eingabe.quelle)
        return db.alle_kurse()

    @app.delete("/api/kurse/{waehrung}/{zr}", status_code=204)
    def kurs_loeschen(waehrung: str, zr: str):
        db.kurs_loeschen(waehrung, zr)
        return Response(status_code=204)

    @app.get("/api/export/{zr}/{datei}")
    def csv_export(zr: str, datei: str):
        jahr, monat = _zeitraum(zr)
        b = berechnen(db.alle_belege(), jahr, monat, umrechner, cfg.versteuerung)
        if datei == "belege.csv":
            inhalt = export.belege_csv(b)
        elif datei == "kennzahlen.csv":
            inhalt = export.kennzahlen_csv(b)
        else:
            raise HTTPException(404)
        name = f"ustva-{zr}-{datei}"
        return Response(inhalt, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.exception_handler(ValueError)
    async def wertfehler(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
