"""Web-Oberfläche und JSON-API."""

from __future__ import annotations

import base64
import re
import secrets
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import bank, export, uebersicht
from .berechnung import berechnen, zeitraum
from .config import Config
from .db import Datenbank
from .fristen import naechste_frist
from .models import Beleg
from .quellen import ImportErgebnis, paperless, verzeichnis
from .steuerfaelle import KENNZAHLEN, STEUERFAELLE, Art, Steuerfall
from .waehrung import Umrechner

STATIC = Path(__file__).parent / "static"
OHNE_FRONTEND = """<!doctype html><html lang="de"><meta charset="utf-8"><title>Finanz-O-Mat</title>
<body style="font-family:sans-serif;max-width:40rem;margin:3rem auto;line-height:1.5">
<h1>Finanz-O-Mat</h1><p>Die API läuft, aber die Oberfläche ist noch nicht gebaut:</p>
<pre>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</pre></body></html>"""
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


class ZuordnungEingabe(BaseModel):
    beleg_id: int | None = None


class IgnorierenEingabe(BaseModel):
    ignoriert: bool = True
    grund: str = ""
    regel: bool = False  # künftig alle Umsätze mit diesem Partner ignorieren


class ImportAnfrage(BaseModel):
    quelle: str = "alle"  # alle | verzeichnis | paperless | konto
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
        belege = db.alle_belege()
        daten = berechnen(belege, jahr, monat, umrechner, cfg.versteuerung).to_dict()
        umsaetze = db.alle_umsaetze()
        if text := bank.hinweis_fehlende_belege(umsaetze, zr):
            daten["hinweise"].insert(0, {"stufe": "warnung", "text": text})
        belege_nach_id = {b.id: b for b in belege}
        daten["umsaetze"] = [
            {**u.to_dict(), "beleg": _beleg_kurz(belege_nach_id.get(u.beleg_id))}
            for u in umsaetze if u.zeitraum == zr
        ]
        return daten

    def _beleg_kurz(b: Beleg | None) -> dict | None:
        if b is None:
            return None
        return {"id": b.id, "partner": b.partner, "beschreibung": b.beschreibung, "rechnungsnummer": b.rechnungsnummer,
                "rechnungsdatum": b.rechnungsdatum.isoformat() if b.rechnungsdatum else None,
                "zahlbetrag": str(b.zahlbetrag()), "waehrung": b.waehrung}

    def _uebersicht(jahr: int) -> dict:
        if not 2000 <= jahr <= 2100:
            raise HTTPException(400, "Jahr ungültig")
        return uebersicht.jahresuebersicht(db.alle_belege(), jahr, umrechner, cfg.versteuerung)

    @app.get("/api/uebersicht/{jahr}")
    def jahresuebersicht(jahr: int):
        return _uebersicht(jahr)

    @app.get("/api/uebersicht/{jahr}/csv")
    def jahresuebersicht_csv(jahr: int):
        return Response(uebersicht.uebersicht_csv(_uebersicht(jahr)), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="uebersicht-{jahr}.csv"'})

    @app.post("/api/import/bank")
    async def bank_import(request: Request):
        roh = await request.body()
        if len(roh) > 10_000_000:
            raise HTTPException(413, "Datei zu groß")
        return bank.importieren(db, cfg, roh.decode("utf-8-sig", errors="replace")).to_dict()

    @app.post("/api/abgleich")
    def abgleich():
        return {"zugeordnet": bank.abgleich_ausfuehren(db)}

    def _umsatz(umsatz_id: int):
        u = db.umsatz(umsatz_id)
        if u is None:
            raise HTTPException(404, "Umsatz nicht gefunden")
        return u

    @app.get("/api/umsaetze/{umsatz_id}/kandidaten")
    def umsatz_kandidaten(umsatz_id: int):
        u = _umsatz(umsatz_id)
        vergeben = {x.beleg_id for x in db.alle_umsaetze() if x.beleg_id is not None and x.id != u.id}
        return [_beleg_kurz(b) for b in bank.kandidaten(u, db.alle_belege(), vergeben, nur_passende=False)[:30]]

    @app.post("/api/umsaetze/{umsatz_id}/zuordnen")
    def umsatz_zuordnen(umsatz_id: int, eingabe: ZuordnungEingabe):
        u = _umsatz(umsatz_id)
        b = _beleg(eingabe.beleg_id) if eingabe.beleg_id is not None else None
        if b is not None and any(x.beleg_id == b.id and x.id != u.id for x in db.alle_umsaetze()):
            raise HTTPException(409, "Der Beleg ist schon einem anderen Umsatz zugeordnet")
        bank.zuordnen(db, u, b)
        return u.to_dict()

    @app.post("/api/umsaetze/{umsatz_id}/ignorieren")
    def umsatz_ignorieren(umsatz_id: int, eingabe: IgnorierenEingabe):
        u = _umsatz(umsatz_id)
        u.ignoriert, u.ignoriert_grund = eingabe.ignoriert, eingabe.grund if eingabe.ignoriert else ""
        if eingabe.ignoriert:
            u.beleg_id = None
        db.umsatz_aktualisieren(u)
        if eingabe.ignoriert and eingabe.regel and u.partner:
            grund = eingabe.grund or "Partner ohne Beleg"
            db.bank_regel_speichern(u.partner, grund)
            for x in db.alle_umsaetze():
                if x.partner.strip().lower() == u.partner.strip().lower() and x.beleg_id is None and not x.ignoriert:
                    x.ignoriert, x.ignoriert_grund = True, grund
                    db.umsatz_aktualisieren(x)
        return u.to_dict()

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
                    quelle_link=alt.quelle_link, hinweise=alt.hinweise, erechnung=alt.erechnung,
                    id=beleg_id)
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
        if anfrage.quelle in ("alle", "konto"):
            if konto := bank.verzeichnis_importieren(db, cfg):
                ergebnisse["konto"] = konto.to_dict()
        if ergebnisse:
            # Neue Belege können zu bereits eingelesenen Umsätzen passen
            zugeordnet = bank.abgleich_ausfuehren(db)
            if zugeordnet:
                ergebnisse.setdefault("konto", bank.BankImport().to_dict())["zugeordnet"] += zugeordnet
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

    if (STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    else:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def ohne_frontend():
            return OHNE_FRONTEND
    return app
