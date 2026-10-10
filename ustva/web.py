"""Web-Oberfläche und JSON-API."""

from __future__ import annotations

import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import aktualisierung, backup, bank, einstellungen, export, monatsexport, taetigkeit, uebersicht
from .berechnung import berechnen, zeitraum
from .config import Config
from .db import Datenbank
from .fristen import naechste_frist
from .models import Beleg, Taetigkeit
from .quellen import ImportErgebnis, neu_einlesen, paperless, verzeichnis
from .steuerfaelle import KENNZAHLEN, STEUERFAELLE, Art, Steuerfall
from .waehrung import Umrechner

STATIC = Path(__file__).parent / "static"
OHNE_FRONTEND = """<!doctype html><html lang="de"><meta charset="utf-8"><title>Finanz-O-Mat</title>
<body style="font-family:sans-serif;max-width:40rem;margin:3rem auto;line-height:1.5">
<h1>Finanz-O-Mat</h1><p>Die API läuft, aber die Oberfläche ist noch nicht gebaut:</p>
<pre>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</pre></body></html>"""
CENT = Decimal("0.01")
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


class EinstellungenEingabe(BaseModel):
    """Nicht gesendete Felder bleiben unverändert; ein leerer Text entfernt den Wert aus der App."""

    paperless_url: str | None = None
    paperless_token: str | None = None
    paperless_tags_ausgang: str | None = None
    paperless_tags_eingang: str | None = None
    eigene_ustid: str | None = None
    bundesland: str | None = None
    paperless_tag_kontoauszug: str | None = None
    paperless_tag_protokoll: str | None = None


class PaperlessTestEingabe(BaseModel):
    paperless_url: str | None = None
    paperless_token: str | None = None


class PasswortEingabe(BaseModel):
    aktuell: str = ""
    benutzer: str = ""
    neu: str


class ImportAnfrage(BaseModel):
    quelle: str = "alle"  # alle | verzeichnis | paperless | konto
    zeitraum: str


class TaetigkeitEingabe(BaseModel):
    datum: date
    stunden: Decimal
    taetigkeit: str = ""
    auftrag: str = ""
    referenz: str = ""
    satz: Decimal | None = None  # leer: Stundensatz aus der Vorlage
    rolle: str = ""
    ort: str = ""
    leistungsart: str = ""

    @field_validator("stunden")
    @classmethod
    def _stunden(cls, v: Decimal) -> Decimal:
        if not Decimal(0) < v <= 24:
            raise ValueError("Stunden zwischen 0 und 24 angeben")
        return v

    @field_validator("taetigkeit", "auftrag", "referenz", "rolle", "ort", "leistungsart")
    @classmethod
    def _text(cls, v: str) -> str:
        return v.strip()


class NeuEinlesenAnfrage(BaseModel):
    ids: list[int] | None = None  # None: alle ungeprüften Belege aus Verzeichnis und Paperless


def create_app(cfg: Config | None = None, db: Datenbank | None = None, umrechner: Umrechner | None = None,
               heute=None) -> FastAPI:
    cfg = cfg or Config.aus_umgebung()
    db = db or Datenbank(cfg.datenbank)
    umrechner = umrechner or Umrechner(db, cfg.ezb_kurse)
    app = FastAPI(title="Finanz-O-Mat", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def _heute() -> date:
        return heute() if callable(heute) else (heute or date.today())

    anmeldung = einstellungen.Anmeldung(cfg, db)

    def aktuell() -> Config:
        """Konfiguration inklusive der Einstellungen aus der Oberfläche."""
        return einstellungen.wirksam(cfg, db)

    @app.middleware("http")
    async def anmeldung_pruefen(request: Request, call_next):
        if not anmeldung.kopf_gueltig(request.headers.get("authorization", "")):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Finanz-O-Mat"'})
        return await call_next(request)

    # Gefundene Protokolle ändern sich nicht mehr; "noch keins" wird nach ein paar Minuten neu gefragt
    protokolle: dict[tuple, tuple[bool, float]] = {}

    def abgegeben_pruefer(c: Config, client: httpx.Client):
        pl = paperless.PaperlessClient(c.paperless_url, c.paperless_token, client)

        def abgegeben(jahr: int, monat: int) -> bool:
            schluessel = (c.paperless_url, c.paperless_tag_protokoll.lower(), jahr, monat)
            if (alt := protokolle.get(schluessel)) and (alt[0] or time.monotonic() - alt[1] < 300):
                return alt[0]
            try:
                ergebnis = monatsexport.protokoll_vorhanden(pl, c.paperless_tag_protokoll, jahr, monat)
            except (paperless.PaperlessFehler, httpx.HTTPError, ValueError):
                ergebnis = False  # ohne Paperless gilt die Frist wie bisher
            protokolle[schluessel] = (ergebnis, time.monotonic())
            return ergebnis

        return abgegeben

    @app.get("/api/status")
    def status():
        c = aktuell()
        if c.paperless_aktiv and c.paperless_tag_protokoll:
            with httpx.Client(timeout=5) as client:
                frist = naechste_frist(_heute(), c.bundesland, c.dauerfristverlaengerung,
                                       abgegeben_pruefer(c, client))
        else:
            frist = naechste_frist(_heute(), c.bundesland, c.dauerfristverlaengerung)
        return {
            "frist": frist.to_dict(),
            "versteuerung": cfg.versteuerung,
            "dauerfristverlaengerung": cfg.dauerfristverlaengerung,
            "bundesland": c.bundesland,
            "verzeichnis": str(cfg.rechnungs_verzeichnis) if cfg.rechnungs_verzeichnis else None,
            "paperless": c.paperless_url if c.paperless_aktiv else None,
            "anmeldung": anmeldung.aktiv,
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
        euro = bank.euro_schaetzer(umrechner)
        daten["umsaetze"] = []
        for u in umsaetze:
            if u.zeitraum != zr:
                continue
            b = belege_nach_id.get(u.beleg_id)
            # Kursaufschlag und Gebühren der Bank bei Fremdwährung; für die Umsatzsteuer ohne Bedeutung.
            kosten = bank.kosten_geldverkehr(u, b, euro) if b and b.waehrung.upper() != "EUR" else None
            daten["umsaetze"].append({**u.to_dict(), "beleg": _beleg_kurz(b),
                                      "kosten_geldverkehr": str(kosten) if kosten else None})
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
        return bank.importieren(db, aktuell(), roh.decode("utf-8-sig", errors="replace"), umrechner).to_dict()

    @app.post("/api/abgleich")
    def abgleich():
        return {"zugeordnet": bank.abgleich_ausfuehren(db, umrechner)}

    def _umsatz(umsatz_id: int):
        u = db.umsatz(umsatz_id)
        if u is None:
            raise HTTPException(404, "Umsatz nicht gefunden")
        return u

    @app.get("/api/umsaetze/{umsatz_id}/kandidaten")
    def umsatz_kandidaten(umsatz_id: int, suche: str = ""):
        u = _umsatz(umsatz_id)
        andere = {x.beleg_id: x for x in db.alle_umsaetze() if x.beleg_id is not None and x.id != u.id}
        euro = bank.euro_schaetzer(umrechner)
        belege = [b for b in db.alle_belege() if bank.passt_zur_suche(b, suche)]
        offen = bank.kandidaten(u, belege, set(andere), nur_passende=False, euro=euro)[:30]
        # Belege, die schon an einem anderen Umsatz hängen, zeigen wir mit an, damit man sie wiederfindet:
        # bei einer Suche alle Treffer, sonst nur die, die genau passen würden.
        vergeben = bank.kandidaten(u, [b for b in belege if b.id in andere], set(),
                                   nur_passende=not suche, euro=euro)[:10]
        return [*(_beleg_kurz(b) for b in offen),
                *({**_beleg_kurz(b), "zugeordnet": andere[b.id].buchungsdatum.isoformat()} for b in vergeben)]

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
        b = db.beleg_anlegen(Beleg(**eingabe.model_dump()))
        bank.abgleich_ausfuehren(db, umrechner)  # kann zu einem schon eingelesenen Umsatz passen
        return b.to_dict()

    @app.get("/api/belege/{beleg_id}")
    def beleg_lesen(beleg_id: int):
        return _beleg(beleg_id).to_dict()

    @app.put("/api/belege/{beleg_id}")
    def beleg_aendern(beleg_id: int, eingabe: BelegEingabe):
        alt = _beleg(beleg_id)
        neu = Beleg(**eingabe.model_dump(), quelle=alt.quelle, quelle_ref=alt.quelle_ref,
                    quelle_link=alt.quelle_link, hinweise=alt.hinweise, erechnung=alt.erechnung,
                    id=beleg_id)
        b = db.beleg_aktualisieren(neu)
        bank.abgleich_ausfuehren(db, umrechner)  # z. B. nachgetragenes Datum oder korrigierter Betrag
        return b.to_dict()

    @app.post("/api/belege/neu-einlesen")
    def belege_neu_einlesen(anfrage: NeuEinlesenAnfrage):
        ergebnis = neu_einlesen.neu_einlesen(db, aktuell(), anfrage.ids)
        if ergebnis.aktualisiert or ergebnis.neu:
            bank.abgleich_ausfuehren(db, umrechner)  # geänderte Beträge können jetzt zu Umsätzen passen
        return ergebnis.to_dict()

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
        c = aktuell()
        if anfrage.quelle in ("alle", "verzeichnis") and c.rechnungs_verzeichnis:
            ergebnisse["verzeichnis"] = verzeichnis.importieren(db, c).to_dict()
        if anfrage.quelle in ("alle", "paperless") and c.paperless_aktiv:
            ergebnisse["paperless"] = paperless.importieren(db, c, jahr, monat).to_dict()
        if anfrage.quelle in ("alle", "konto"):
            if konto := bank.verzeichnis_importieren(db, c):
                ergebnisse["konto"] = konto.to_dict()
        if ergebnisse:
            # Neue Belege können zu bereits eingelesenen Umsätzen passen
            zugeordnet = bank.abgleich_ausfuehren(db, umrechner)
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

    @app.get("/api/export/{zr}/unterlagen.zip")
    def unterlagen_zip(zr: str):
        jahr, monat = _zeitraum(zr)
        belege = db.alle_belege()
        b = berechnen(belege, jahr, monat, umrechner, cfg.versteuerung)
        alle = db.alle_umsaetze()
        inhalt = monatsexport.monats_zip(aktuell(), b, [u for u in alle if u.zeitraum == zr],
                                         {x.id: x for x in belege if x.id},
                                         andere_umsaetze=[u for u in alle if u.zeitraum != zr])
        return Response(inhalt, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="Unterlagen-{zr}.zip"'})

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

    # Tätigkeitsnachweis
    @app.get("/api/taetigkeiten")
    def taetigkeiten(zeitraum: str):
        jahr, monat = _zeitraum(zeitraum)
        v = taetigkeit.vorlage_laden(db)
        bundesland = aktuell().bundesland
        eintraege = db.taetigkeiten(zeitraum)
        betraege = [taetigkeit.betrag(t, v) for t in eintraege]
        return {
            "eintraege": [{**t.to_dict(), "betrag": None if b is None else str(b.quantize(CENT))}
                          for t, b in zip(eintraege, betraege, strict=True)],
            "stunden": str(sum((t.stunden for t in eintraege), Decimal(0))),
            "betrag": str(sum((b for b in betraege if b is not None), Decimal(0)).quantize(CENT)),
            "fehlende_tage": [t.isoformat() for t in
                              taetigkeit.fehlende_tage(eintraege, jahr, monat, _heute(), bundesland)],
            "vorschlag": taetigkeit.vorschlag(db, v, jahr, monat, bundesland),
            "texte": taetigkeit.fruehere_texte(db),
        }

    def _taetigkeit(taetigkeit_id: int) -> Taetigkeit:
        t = db.taetigkeit(taetigkeit_id)
        if t is None:
            raise HTTPException(404, "Eintrag nicht gefunden")
        return t

    @app.post("/api/taetigkeiten", status_code=201)
    def taetigkeit_anlegen(eingabe: TaetigkeitEingabe):
        t = Taetigkeit(**eingabe.model_dump())
        if t.satz is None:  # Satz festhalten, damit ein neuer Stundensatz alte Monate nicht ändert
            t.satz = taetigkeit.vorlage_laden(db).stundensatz
        return db.taetigkeit_anlegen(t).to_dict()

    @app.put("/api/taetigkeiten/{taetigkeit_id:int}")
    def taetigkeit_aendern(taetigkeit_id: int, eingabe: TaetigkeitEingabe):
        alt = _taetigkeit(taetigkeit_id)
        t = Taetigkeit(**eingabe.model_dump(), id=taetigkeit_id)
        if t.satz is None:
            t.satz = alt.satz if alt.satz is not None else taetigkeit.vorlage_laden(db).stundensatz
        return db.taetigkeit_aktualisieren(t).to_dict()

    @app.delete("/api/taetigkeiten/{taetigkeit_id:int}", status_code=204)
    def taetigkeit_loeschen(taetigkeit_id: int):
        _taetigkeit(taetigkeit_id)
        db.taetigkeit_loeschen(taetigkeit_id)
        return Response(status_code=204)

    @app.get("/api/taetigkeiten/vorlage")
    def taetigkeit_vorlage():
        return taetigkeit.vorlage_laden(db).to_dict()

    @app.put("/api/taetigkeiten/vorlage")
    def taetigkeit_vorlage_speichern(eingabe: dict):
        return taetigkeit.vorlage_speichern(db, taetigkeit.Vorlage.aus_dict(eingabe)).to_dict()

    @app.post("/api/taetigkeiten/import")
    async def taetigkeit_import(request: Request):
        roh = await request.body()
        if len(roh) > 20_000_000:
            raise HTTPException(413, "Datei zu groß")
        return taetigkeit.importieren(db, roh).to_dict()

    @app.get("/api/taetigkeiten/nachweis/{zr}")
    def taetigkeit_nachweis(zr: str):
        jahr, monat = _zeitraum(zr)
        inhalt = taetigkeit.nachweis_xlsx(taetigkeit.vorlage_laden(db), db.taetigkeiten(zr), jahr, monat)
        name = taetigkeit.dateiname(jahr, monat)
        ascii_name = f"Taetigkeitsnachweis-{zr}.xlsx"
        return Response(inhalt, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition":
                                 f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"})

    def _einstellungen() -> dict:
        c = aktuell()

        def feld(name: str, wert: str) -> dict:
            return {"wert": wert, "quelle": einstellungen.quelle(cfg, db, name)}

        benutzer = anmeldung.zugang()[0]
        return {
            "paperless_url": feld("paperless_url", c.paperless_url),
            # Das Token selbst wird nie an den Browser zurückgegeben.
            "paperless_token": {"gesetzt": bool(c.paperless_token),
                                "quelle": einstellungen.quelle(cfg, db, "paperless_token")},
            "paperless_tags_ausgang": feld("paperless_tags_ausgang", ", ".join(c.paperless_tags_ausgang)),
            "paperless_tags_eingang": feld("paperless_tags_eingang", ", ".join(c.paperless_tags_eingang)),
            "paperless_tag_kontoauszug": feld("paperless_tag_kontoauszug", c.paperless_tag_kontoauszug),
            "paperless_tag_protokoll": feld("paperless_tag_protokoll", c.paperless_tag_protokoll),
            "eigene_ustid": feld("eigene_ustid", c.eigene_ustid),
            "bundesland": feld("bundesland", c.bundesland),
            "bundeslaender": [{"wert": k, "name": v} for k, v in einstellungen.BUNDESLAENDER.items()],
            "anmeldung": {"aktiv": anmeldung.aktiv, "benutzer": benutzer, "quelle": anmeldung.quelle(),
                          "min_laenge": einstellungen.MIN_PASSWORT},
        }

    @app.get("/api/einstellungen")
    def einstellungen_lesen():
        return _einstellungen()

    @app.put("/api/einstellungen")
    def einstellungen_speichern(eingabe: EinstellungenEingabe):
        neu = {k: v for k, v in eingabe.model_dump().items() if v is not None}
        geprueft = {k: einstellungen.pruefen(k, v) for k, v in neu.items()}
        for schluessel, wert in geprueft.items():
            db.einstellung_setzen(schluessel, wert or None)
        return _einstellungen()

    @app.post("/api/einstellungen/paperless-test")
    def paperless_testen(eingabe: PaperlessTestEingabe):
        c = aktuell()
        url = einstellungen.pruefen("paperless_url", eingabe.paperless_url or c.paperless_url)
        token = eingabe.paperless_token or c.paperless_token
        if not url or not token:
            raise HTTPException(400, "Bitte URL und Token angeben.")
        try:
            with httpx.Client(timeout=10) as client:
                tags = paperless.PaperlessClient(url, token, client).namen("/api/tags/")
        except paperless.PaperlessFehler as e:
            raise HTTPException(400, str(e)) from e
        return {"text": f"Verbindung klappt, {len(tags)} Tags gefunden."}

    @app.post("/api/passwort")
    def passwort_aendern(eingabe: PasswortEingabe):
        if anmeldung.aktiv and not anmeldung.pruefe(anmeldung.zugang()[0], eingabe.aktuell):
            raise HTTPException(403, "Das aktuelle Passwort stimmt nicht.")
        anmeldung.setzen(eingabe.benutzer or anmeldung.zugang()[0], eingabe.neu)
        return _einstellungen()["anmeldung"]

    app.include_router(aktualisierung.router(cfg.update_verzeichnis))
    app.include_router(backup.router(cfg.backup_verzeichnis))

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
