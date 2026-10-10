"""Belege aus Paperless-ngx über die REST-API einlesen.

Ob ein Dokument eine Ausgangs- oder Eingangsrechnung ist, entscheidet ein Tag
oder Dokumenttyp (Standard: "Ausgangsrechnung" / "Eingangsrechnung"). Beträge
kommen bevorzugt aus benutzerdefinierten Feldern (z. B. ein Geldfeld "Netto"),
sonst aus dem OCR-Text des Dokuments.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from ..config import Config
from ..db import Datenbank
from ..extraktion import betrag_parsen, erkennen, steuerfall_vorschlagen
from ..fristen import monat_plus
from ..steuerfaelle import Art
from . import ImportErgebnis, beleg_aus_erkennung

RE_GELDFELD = re.compile(r"^([A-Z]{3})?\s?(-?[\d.,]+)$")


class PaperlessFehler(Exception):
    pass


def geldfeld_parsen(wert) -> tuple[Decimal, str | None] | None:
    """Paperless speichert Geldfelder als z. B. "EUR123.45" oder "123.45"."""
    if wert in (None, ""):
        return None
    if isinstance(wert, (int, float)):
        return Decimal(str(wert)), None
    m = RE_GELDFELD.match(str(wert).strip())
    if not m:
        return None
    betrag = betrag_parsen(m.group(2))
    return (betrag, m.group(1)) if betrag is not None else None


def _datum(wert: str | None) -> date | None:
    if not wert:
        return None
    try:
        return date.fromisoformat(str(wert)[:10])
    except ValueError:
        return None


class PaperlessClient:
    def __init__(self, url: str, token: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        self.client.headers.update({"Authorization": f"Token {token}", "Accept": "application/json; version=5"})

    def _alle(self, pfad: str, params: dict | None = None) -> list[dict]:
        ergebnisse: list[dict] = []
        naechste: str | None = f"{self.url}{pfad}"
        params = {"page_size": 100, **(params or {})}
        while naechste:
            try:
                antwort = self.client.get(naechste, params=params)
                antwort.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise PaperlessFehler("Paperless lehnt den Zugriff ab, bitte Token prüfen") from e
                raise PaperlessFehler(f"Paperless antwortet mit Fehler {e.response.status_code}") from e
            except httpx.HTTPError as e:
                raise PaperlessFehler(f"Paperless nicht erreichbar: {e}") from e
            daten = antwort.json()
            ergebnisse.extend(daten.get("results", []))
            naechste = daten.get("next")
            params = None  # "next" enthält die Parameter bereits
        return ergebnisse

    def namen(self, pfad: str) -> dict[int, str]:
        return {e["id"]: e["name"] for e in self._alle(pfad)}

    def felder(self) -> dict[int, str]:
        try:
            return self.namen("/api/custom_fields/")
        except PaperlessFehler:
            return {}  # ältere Paperless-Versionen ohne benutzerdefinierte Felder

    def dokumente(self, von: date, bis_exklusiv: date) -> list[dict]:
        return self._alle("/api/documents/", {
            "created__gte": von.isoformat(),
            "created__lt": bis_exklusiv.isoformat(),
            "ordering": "created",
        })


def importieren(
    db: Datenbank, cfg: Config, jahr: int, monat: int, client: httpx.Client | None = None
) -> ImportErgebnis:
    """Liest die Rechnungen eines Monats ein (nach Ausstellungsdatum in Paperless).

    Die zwei Vormonate werden mit abgefragt, damit bei Ist-Versteuerung auch
    ältere Rechnungen ankommen, die erst in diesem Monat bezahlt wurden. Bereits
    eingelesene Dokumente werden übersprungen.
    """
    ergebnis = ImportErgebnis()
    if not cfg.paperless_aktiv:
        ergebnis.fehler.append("Paperless ist nicht konfiguriert (PAPERLESS_URL und PAPERLESS_TOKEN)")
        return ergebnis
    pl = PaperlessClient(cfg.paperless_url, cfg.paperless_token, client)
    try:
        tags = pl.namen("/api/tags/")
        typen = pl.namen("/api/document_types/")
        korrespondenten = pl.namen("/api/correspondents/")
        felder = pl.felder()
        vj, vm = monat_plus(jahr, monat, -2)
        nj, nm = monat_plus(jahr, monat, 1)
        dokumente = pl.dokumente(date(vj, vm, 1), date(nj, nm, 1))
    except PaperlessFehler as e:
        ergebnis.fehler.append(str(e))
        return ergebnis

    ausgang = {n.lower() for n in cfg.paperless_tags_ausgang}
    eingang = {n.lower() for n in cfg.paperless_tags_eingang}

    for dok in dokumente:
        namen = {tags.get(t, "").lower() for t in dok.get("tags", [])}
        if dok.get("document_type") is not None:
            namen.add(typen.get(dok["document_type"], "").lower())
        if namen & ausgang:
            art = Art.AUSGANG
        elif namen & eingang:
            art = Art.EINGANG
        else:
            ergebnis.uebersprungen += 1
            continue
        ref = str(dok["id"])
        if db.beleg_vorhanden("paperless", ref):
            ergebnis.vorhanden += 1
            continue
        try:
            werte = {felder.get(f["field"], "").lower(): f.get("value") for f in dok.get("custom_fields", [])}
            erkennung = erkennen(dok.get("content") or "", art, cfg.eigene_ustid)
            _felder_uebernehmen(erkennung, werte, cfg)
            beleg = beleg_aus_erkennung(
                erkennung, art,
                quelle="paperless", quelle_ref=ref,
                quelle_link=f"{cfg.paperless_url}/documents/{dok['id']}/details",
                partner=korrespondenten.get(dok.get("correspondent"), "") if dok.get("correspondent") else "",
                beschreibung=dok.get("title") or "",
            )
            if zahlung := _datum(werte.get(cfg.paperless_feld_zahlungsdatum.lower())):
                beleg.zahlungsdatum = zahlung
            if beleg.rechnungsdatum is None:
                beleg.rechnungsdatum = _datum(dok.get("created"))
            db.beleg_anlegen(beleg)
            ergebnis.neu += 1
        except Exception as e:  # ein fehlerhaftes Dokument soll den Import nicht abbrechen
            ergebnis.fehler.append(f"Dokument {dok.get('id')} ({dok.get('title')}): {e}")
    return ergebnis


def _felder_uebernehmen(e, werte: dict, cfg: Config) -> None:
    """Benutzerdefinierte Felder haben Vorrang vor dem OCR-Text."""
    netto = geldfeld_parsen(werte.get(cfg.paperless_feld_netto.lower()))
    brutto = geldfeld_parsen(werte.get(cfg.paperless_feld_brutto.lower()))
    steuer = geldfeld_parsen(werte.get(cfg.paperless_feld_steuer.lower()))
    satz = werte.get(cfg.paperless_feld_steuersatz.lower())
    if satz not in (None, ""):
        try:
            e.steuersatz = Decimal(str(satz).replace("%", "").replace(",", ".").strip())
        except InvalidOperation:
            pass
    for feld in (netto, brutto, steuer):
        if feld and feld[1]:
            e.waehrung = feld[1]
    if steuer:
        e.steuer = steuer[0]
    if netto:
        e.netto = netto[0]
        if brutto and not steuer:
            e.steuer = brutto[0] - netto[0]
    elif brutto:
        e.brutto = brutto[0]
        if e.steuer is not None:
            e.netto = brutto[0] - e.steuer
        elif e.steuersatz is not None:
            e.netto = (brutto[0] * 100 / (100 + e.steuersatz)).quantize(Decimal("0.01"))
        else:
            e.netto = brutto[0]
    if netto or brutto:
        e.hinweise = [h for h in e.hinweise if not h.startswith("Kein Betrag")]
        e.steuerfall = steuerfall_vorschlagen(e)
