"""API für den Reiter „Konten“: Salden und Umsätze aller über Enable Banking verbundenen Konten, dazu der Bestand
des ING-Direkt-Depots über FinTS (ustva/depot.py).

Nur eine Übersicht für den Inhaber. Die Buchhaltung (Kontoumsätze, Belegabgleich) bleibt davon unberührt; dorthin
kommen nur die Konten, die mit ``bank konto … --an`` eingeschaltet sind (siehe ustva/kontoabruf.py).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import camt, depot, monitoring
from . import kontoabruf as ka
from .config import Config
from .db import Datenbank
from .models import BankKonto

log = logging.getLogger(__name__)


def _konto(k: BankKonto, jetzt: datetime) -> dict:
    frei = ka.naechster_freier_abruf(k, jetzt)
    return {
        "schluessel": k.schluessel,
        "name": k.bezeichnung or k.bank,
        "bank": k.bank,
        "bezeichnung": k.bezeichnung,
        "iban": k.iban,
        "waehrung": k.waehrung,
        "saldo": k.saldo or None,
        "saldo_stand": k.saldo_stand,
        "abgerufen": k.abgerufen,
        "freigabe_bis": k.gueltig_bis,
        "freigabe_tage": ka.tage_gueltig(k, jetzt),
        "abrufe_frei": ka.abrufe_frei(k, jetzt),
        "naechster_frei": frei.isoformat(timespec="seconds") if frei else None,
        "fehler": k.fehler,
        "buchhaltung": k.aktiv,
    }


def _depots(db: Datenbank) -> list[dict]:
    return [{"schluessel": d["schluessel"], "bank": d["bank"], "name": d["name"], "waehrung": d["waehrung"],
             "wert": d["wert"] or None, "einstandswert": d["einstandswert"] or None, "stand": d["stand"],
             "abgerufen": d["abgerufen"], "positionen": d["positionen"]} for d in db.depots()]


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
        liste = [_konto(k, zeit) for k in db.bank_konten()]
        summen: dict[str, Decimal] = {}
        for k in db.bank_konten():
            if k.saldo:
                summen[k.waehrung] = summen.get(k.waehrung, Decimal(0)) + Decimal(k.saldo)
        eingerichtet = bool(cfg.enable_banking_app_id and cfg.enable_banking_schluessel)
        zustand = db.fints_zustand(depot.ZUGANG) or {}
        return {
            "eingerichtet": eingerichtet,
            "depot": {"eingerichtet": depot.eingerichtet(cfg), "fehler": zustand.get("fehler", ""),
                      "freigabe": bool(zustand.get("freigabe")), "versucht": zustand.get("versucht", "")},
            "depots": _depots(db),
            "zeiten": cfg.kontoabruf_zeiten if eingerichtet or depot.eingerichtet(cfg) else [],
            "abrufe_je_tag": ka.ABRUFE_JE_TAG,
            "summen": {w: str(b) for w, b in summen.items()},
            "konten": sorted(liste, key=lambda k: (k["bank"].lower(), k["name"].lower(), k["iban"])),
        }

    @r.get("/api/konten/buchungen")
    def buchungen(konto: str | None = None):
        """Vorgemerkte Umsätze (noch nicht gebucht, bei jedem Abruf ersetzt) zuerst, dann die gebuchten."""
        vorgemerkt = [z | {"pruefsumme": f"vorgemerkt:{z['konto']}:{z['nr']}", "vorgemerkt": True}
                      for z in db.konto_vorgemerkt(konto or None)]
        return vorgemerkt + [z | {"vorgemerkt": False} for z in db.konto_buchungen(konto or None)]

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
    async def abrufen():
        cfg = aktuell()
        try:
            eb = eb_fabrik(cfg)
        except ka.KontoabrufFehler as e:
            raise HTTPException(400, str(e)) from e
        e = await run_in_threadpool(ka.abrufen, db, cfg, eb, jetzt=_jetzt())
        monitoring.kontoabruf_zaehlen(e, "knopf")
        return {"zugeordnet": e.zugeordnet, "konten": [
            {"schluessel": a.konto.schluessel, "name": a.konto.bezeichnung or a.konto.bank, "neu": a.uebersicht,
             "fehler": a.fehler, "uebersprungen": a.uebersprungen} for a in e.konten]}

    return r
