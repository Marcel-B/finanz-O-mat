"""API für den Reiter „Konten“: Salden und Umsätze aller über Enable Banking verbundenen Konten.

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

from . import camt, monitoring
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


def router(db: Datenbank, aktuell: Callable[[], Config],
           eb_fabrik: Callable[[Config], ka.EnableBanking] | None = None, jetzt=None) -> APIRouter:
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
        return {
            "eingerichtet": eingerichtet,
            "zeiten": cfg.kontoabruf_zeiten if eingerichtet else [],
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
