"""API für den Reiter „Konten“: Salden und Umsätze aller über Enable Banking verbundenen Konten, dazu der Bestand
des ING-Direkt-Depots über FinTS (ustva/depot.py).

Nur eine Übersicht für den Inhaber. Die Buchhaltung (Kontoumsätze, Belegabgleich) bleibt davon unberührt; dorthin
kommen nur die Konten, die mit ``bank konto … --an`` eingeschaltet sind (siehe ustva/kontoabruf.py).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import camt, depot, ing_konten, kontoverlauf, monitoring
from . import kontoabruf as ka
from .config import Config
from .db import Datenbank, stichtag
from .models import BankKonto

log = logging.getLogger(__name__)


class AnzeigeAenderung(BaseModel):
    """Nur die übergebenen Felder ändern sich."""

    schluessel: str
    name: str | None = None  # leer: wieder der Name der Bank
    farbe: str | None = None  # #rrggbb, leer: keine


class Reihenfolge(BaseModel):
    schluessel: list[str]


NAME_LAENGE = 60
FARBE = re.compile(r"#[0-9a-f]{6}")


# BIC der Banken, die der Reiter kennt, nach Land und Bankkennung in der IBAN. Gilt, solange die Bank selbst keine
# BIC mitgeschickt hat (über FinTS kommt keine, Enable Banking schickt sie erst mit der nächsten Freigabe).
BEKANNTE_BIC = {
    ("DE", depot.ING_BLZ): depot.ING_BIC,
    ("DE", "10011001"): "NTSBDEB1XXX",  # N26
    ("NL", "BUNQ"): "BUNQNL2AXXX",  # bunq
}


def bic(k: BankKonto) -> str:
    if k.bic:
        return k.bic
    land = k.iban[:2]
    kennung = k.iban[4:12] if land == "DE" else k.iban[4:8]
    return BEKANNTE_BIC.get((land, kennung), "")


def _anzeige(anzeige: dict[str, dict], schluessel: str) -> tuple[str, str, int | None]:
    a = anzeige.get(schluessel) or {}
    return a.get("name") or "", a.get("farbe") or "", a.get("reihenfolge")


def _reihenfolge(eintrag: dict) -> tuple:
    """Erst die von Hand sortierten nach ihrem Platz, dann die übrigen nach Bank und Name."""
    platz = eintrag.pop("_platz")
    return (platz is None, platz or 0, eintrag["bank"].lower(), eintrag["name"].lower(),
            eintrag.get("iban") or eintrag["schluessel"])


def _konto(k: BankKonto, jetzt: datetime, anzeige: dict[str, dict], vorgemerkt: dict[str, list[Decimal]]) -> dict:
    frei = ka.naechster_freier_abruf(k, jetzt)
    offen = vorgemerkt.get(k.schluessel, [])
    name, farbe, platz = _anzeige(anzeige, k.schluessel)
    return {
        "schluessel": k.schluessel,
        "name": name or k.bezeichnung or k.bank,
        "eigener_name": name,
        "farbe": farbe,
        "_platz": platz,
        "bank": k.bank,
        "bezeichnung": k.bezeichnung,
        "iban": k.iban,
        "bic": bic(k),
        "waehrung": k.waehrung,
        "saldo": k.saldo or None,
        "saldo_stand": k.saldo_stand,
        # noch nicht gebuchte Umsätze (Summe, meist negativ); der Saldo der Bank ist der gebuchte
        "vorgemerkt": str(sum(offen, Decimal(0))) if offen else None,
        "vorgemerkt_anzahl": len(offen),
        "abgerufen": k.abgerufen,
        "freigabe_bis": k.gueltig_bis,
        "freigabe_tage": ka.tage_gueltig(k, jetzt),
        "abrufe_frei": ka.abrufe_frei(k, jetzt),
        "naechster_frei": frei.isoformat(timespec="seconds") if frei else None,
        "fehler": k.fehler,
        "buchhaltung": k.aktiv,
        "fints": k.fints,  # letzter Abruf über FinTS (nur ING)
        "ing": ing_konten.ing(k),
        "nur_fints": k.sitzung == ka.FINTS_SITZUNG,  # nicht über Enable Banking verbunden
    }


def _depots(db: Datenbank, anzeige: dict[str, dict]) -> list[dict]:
    liste = []
    for d in db.depots():
        name, farbe, platz = _anzeige(anzeige, d["schluessel"])
        liste.append({"schluessel": d["schluessel"], "bank": d["bank"], "name": name or d["name"],
                      "eigener_name": name, "farbe": farbe, "_platz": platz, "bezeichnung": d["name"],
                      "waehrung": d["waehrung"], "wert": d["wert"] or None,
                      "einstandswert": d["einstandswert"] or None, "stand": d["stand"], "abgerufen": d["abgerufen"],
                      "positionen": d["positionen"]})
    return sorted(liste, key=_reihenfolge)


def router(db: Datenbank, aktuell: Callable[[], Config],
           eb_fabrik: Callable[[Config], ka.EnableBanking] | None = None, jetzt=None,
           depot_fabrik: Callable[[Config, bytes | None], depot.IngFints] | None = None) -> APIRouter:
    r = APIRouter()
    eb_fabrik = eb_fabrik or ka.EnableBanking.aus_config

    def _jetzt() -> datetime:
        return jetzt() if callable(jetzt) else datetime.now(UTC)

    @r.get("/api/konten")
    def konten():
        cfg = aktuell()
        zeit = _jetzt()
        anzeige = db.konto_anzeige()
        alle = db.bank_konten()
        vorgemerkt: dict[str, list[Decimal]] = {}
        for z in db.konto_vorgemerkt():
            vorgemerkt.setdefault(z["konto"], []).append(Decimal(z["betrag"]))
        liste = sorted((_konto(k, zeit, anzeige, vorgemerkt) for k in alle), key=_reihenfolge)
        summen: dict[str, Decimal] = {}
        summen_vorgemerkt: dict[str, Decimal] = {}
        for k in alle:
            if k.saldo:
                summen[k.waehrung] = summen.get(k.waehrung, Decimal(0)) + Decimal(k.saldo)
            if k.schluessel in vorgemerkt:
                summen_vorgemerkt[k.waehrung] = summen_vorgemerkt.get(k.waehrung, Decimal(0)) + sum(
                    vorgemerkt[k.schluessel], Decimal(0))
        eingerichtet = bool(cfg.enable_banking_app_id and cfg.enable_banking_schluessel)
        zustand = db.fints_zustand(depot.ZUGANG) or {}
        return {
            "eingerichtet": eingerichtet,
            "depot": {"eingerichtet": depot.eingerichtet(cfg), "fehler": zustand.get("fehler", ""),
                      "freigabe": bool(zustand.get("freigabe")), "versucht": zustand.get("versucht", ""),
                      "abgelehnt": bool(zustand.get("abgelehnt"))},
            "depots": _depots(db, anzeige),
            # ING-Konten über FinTS: beim Öffnen, mit „Jetzt abrufen“ und stündlich (ustva/ing_konten.py)
            "ing": {"eingerichtet": depot.eingerichtet(cfg),
                    "versucht": zustand.get("konten", ""),
                    "beim_oeffnen": int(ing_konten.BEIM_OEFFNEN.total_seconds())},
            "zeiten": cfg.kontoabruf_zeiten if eingerichtet or depot.eingerichtet(cfg) else [],
            "abrufe_je_tag": ka.ABRUFE_JE_TAG,
            "summen": {w: str(b) for w, b in summen.items()},
            "summen_vorgemerkt": {w: str(b) for w, b in summen_vorgemerkt.items()},
            "konten": liste,
        }

    @r.get("/api/konten/buchungen")
    def buchungen(konto: str | None = None):
        """Vorgemerkte Umsätze (noch nicht gebucht, bei jedem Abruf ersetzt) zuerst, dann die gebuchten."""
        vorgemerkt = [z | {"pruefsumme": f"vorgemerkt:{z['konto']}:{z['nr']}", "vorgemerkt": True}
                      for z in db.konto_vorgemerkt(konto or None)]
        return vorgemerkt + [z | {"vorgemerkt": False} for z in db.konto_buchungen(konto or None)]

    def _bekannt() -> set[str]:
        return {k.schluessel for k in db.bank_konten()} | {d["schluessel"] for d in db.depots()}

    @r.put("/api/konten/anzeige")
    @r.put("/api/konten/name")  # alter Name des Endpunkts
    def anzeige_setzen(daten: AnzeigeAenderung):
        """Eigener Name (leer = wieder der Name der Bank) und Farbe (leer = keine) für ein Konto oder Depot."""
        if daten.schluessel not in _bekannt():
            raise HTTPException(404, "Konto nicht gefunden")
        name = None if daten.name is None else " ".join(daten.name.split())
        farbe = None if daten.farbe is None else daten.farbe.strip().lower()
        if name is not None and len(name) > NAME_LAENGE:
            raise HTTPException(400, f"Name zu lang (höchstens {NAME_LAENGE} Zeichen)")
        if farbe and not FARBE.fullmatch(farbe):
            raise HTTPException(400, "Farbe als #rrggbb angeben")
        if name is not None:
            db.konto_name_setzen(daten.schluessel, name)
        if farbe is not None:
            db.konto_farbe_setzen(daten.schluessel, farbe)
        name_neu, farbe_neu, _ = _anzeige(db.konto_anzeige(), daten.schluessel)
        return {"schluessel": daten.schluessel, "name": name_neu, "farbe": farbe_neu}

    @r.put("/api/konten/reihenfolge")
    def reihenfolge_setzen(daten: Reihenfolge):
        """Reihenfolge in der Kontenliste: die Schlüssel der Konten (oder der Depots) von oben nach unten."""
        if unbekannt := set(daten.schluessel) - _bekannt():
            raise HTTPException(404, f"Konto nicht gefunden: {', '.join(sorted(unbekannt))}")
        if len(set(daten.schluessel)) != len(daten.schluessel):
            raise HTTPException(400, "Konto doppelt in der Reihenfolge")
        db.konto_reihenfolge_setzen(daten.schluessel)
        return {"schluessel": daten.schluessel}

    @r.get("/api/konten/verlauf")
    def verlauf(konto: str | None = None):
        """Kontostand je Tag, zurückgerechnet aus den Umsätzen (ustva/kontoverlauf.py). Ohne ``konto`` die Summe
        aller Konten in Euro."""
        heute = _jetzt().astimezone(ZoneInfo(ka.ZEITZONE)).date()
        konten = db.bank_konten()
        if konto:
            konten = [k for k in konten if k.schluessel == konto]
            if not konten:
                raise HTTPException(404, "Konto nicht gefunden")
        else:
            konten = [k for k in konten if k.waehrung == "EUR"]
        verlaeufe = [_verlauf(k, heute) for k in konten]
        punkte = verlaeufe[0] if konto else kontoverlauf.summe(verlaeufe)
        return {"waehrung": konten[0].waehrung if konto else "EUR",
                "punkte": [{"tag": t.isoformat(), "wert": str(w)} for t, w in punkte]}

    def _verlauf(k: BankKonto, heute: date) -> kontoverlauf.Verlauf:
        buchungen = [(date.fromisoformat(z["buchungsdatum"]), Decimal(z["betrag"]))
                     for z in db.konto_buchungen(k.schluessel)]
        salden = {date.fromisoformat(t): Decimal(s) for t, s in db.konto_salden(k.schluessel).items()}
        stand = date.fromisoformat(stichtag(k.saldo_stand)) if k.saldo and k.saldo_stand else None
        return kontoverlauf.konto(Decimal(k.saldo) if k.saldo else None, stand, buchungen, salden, heute)

    @r.get("/api/konten/depot")
    def depot_bestand(schluessel: str):
        """Positionen eines Depots (größte zuerst) und sein Wert je Tag."""
        if not any(d["schluessel"] == schluessel for d in db.depots()):
            raise HTTPException(404, "Depot nicht gefunden")
        return {"positionen": db.depot_positionen(schluessel), "verlauf": db.depot_werte(schluessel)}

    @r.post("/api/konten/depot/abrufen")
    async def depot_abrufen():
        """Holt den Bestand; verlangt die ING eine Freigabe in der App, wartet die Anfrage bis zu zwei Minuten."""
        cfg = aktuell()
        if not depot.eingerichtet(cfg):
            raise HTTPException(400, "Depotabruf nicht eingerichtet (FINTS_PRODUKT_ID, ING_FINTS_ZUGANG, "
                                     "ING_FINTS_PIN)")
        zustand = (db.fints_zustand(depot.ZUGANG) or {}).get("daten")
        zugang = depot_fabrik(cfg, zustand) if depot_fabrik else depot.IngFints(cfg, zustand)
        a = await run_in_threadpool(depot.abrufen, db, cfg, zugang, jetzt=_jetzt())
        monitoring.depotabruf_zaehlen(a, "knopf")
        if a.fehler:
            log.warning("Depotabruf: %s", a.fehler)
        return {"fehler": a.fehler, "freigabe": a.freigabe,
                "depots": [{"schluessel": d.schluessel, "name": d.name, "positionen": len(d.positionen)}
                           for d in a.depots]}

    @r.post("/api/konten/ing/abrufen")
    async def ing_abrufen(automatisch: bool = False):
        """Saldo und Umsätze der ING-Konten über FinTS, ohne das Limit von PSD2.

        ``automatisch`` (beim Öffnen des Reiters): nur, wenn der letzte Abruf älter als zwei Minuten ist, und ohne
        erneut nach einer Freigabe in der App zu fragen, auf die die ING schon wartet.
        """
        cfg = aktuell()
        if not depot.eingerichtet(cfg):
            raise HTTPException(400, "FinTS nicht eingerichtet (FINTS_PRODUKT_ID, ING_FINTS_ZUGANG, ING_FINTS_PIN)")
        zustand = (db.fints_zustand(depot.ZUGANG) or {}).get("daten")
        zugang = depot_fabrik(cfg, zustand) if depot_fabrik else None
        a = await run_in_threadpool(ing_konten.abrufen, db, cfg, zugang, jetzt=_jetzt(), hintergrund=automatisch,
                                    wenn_aelter=ing_konten.BEIM_OEFFNEN if automatisch else None)
        monitoring.ing_abruf_zaehlen(a, "oeffnen" if automatisch else "knopf")
        if a.fehler:
            log.warning("ING-Konten über FinTS: %s", a.fehler)
        return {"fehler": a.fehler, "freigabe": a.freigabe, "uebersprungen": a.uebersprungen, "neu": a.neu,
                "vorgemerkt": a.vorgemerkt, "konten": a.konten}

    @r.post("/api/konten/einlesen")
    async def einlesen(request: Request):
        """Alte Kontoauszüge (CAMT.053, z. B. aus MoneyMoney) in die Übersicht; der Rumpf ist die XML-Datei."""
        roh = await request.body()
        if len(roh) > 50_000_000:
            raise HTTPException(413, "Datei zu groß")
        try:
            ergebnisse = await run_in_threadpool(camt.einlesen, db, roh)
        except camt.CamtFehler as e:
            raise HTTPException(400, str(e)) from e
        for e in ergebnisse:
            if e.fehler:
                log.warning("CAMT-Import %s: %s", e.iban[-4:], e.fehler)
            else:
                log.info("CAMT-Import %s: %d neu, %d schon vorhanden", e.konto, e.neu, e.vorhanden)
        return {"auszuege": [e.to_dict() for e in ergebnisse]}

    @r.post("/api/konten/abrufen")
    async def abrufen(request: Request, automatisch: bool = False):
        """Saldo und Umsätze über Enable Banking, mit den PSU-Headern des Browsers (dann ohne das Tageslimit).

        ``automatisch`` (beim Öffnen des Reiters): nur Konten, deren letzter Abruf älter als zwei Minuten ist, und
        nie ein Abruf, der zum Limit zählt.
        """
        cfg = aktuell()
        if automatisch and not cfg.enable_banking_app_id:
            return {"zugeordnet": 0, "konten": []}
        try:
            eb = eb_fabrik(cfg)
        except ka.KontoabrufFehler as e:
            raise HTTPException(400, str(e)) from e
        psu = ka.psu_header(request.client.host if request.client else "", request.headers)
        e = await run_in_threadpool(ka.abrufen, db, cfg, eb, jetzt=_jetzt(), psu=psu,
                                    wenn_aelter=ka.ANWESEND_ABSTAND if automatisch else None)
        monitoring.kontoabruf_zaehlen(e, "oeffnen" if automatisch else "knopf")
        namen = db.konto_namen()
        return {"zugeordnet": e.zugeordnet, "konten": [
            {"schluessel": a.konto.schluessel,
             "name": namen.get(a.konto.schluessel) or a.konto.bezeichnung or a.konto.bank, "neu": a.uebersicht,
             "fehler": a.fehler, "uebersprungen": a.uebersprungen} for a in e.konten]}

    return r
