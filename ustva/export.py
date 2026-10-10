"""CSV-Export im deutschen Format (Semikolon, Dezimalkomma, UTF-8 mit BOM für Excel)."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from .berechnung import Bericht
from .steuerfaelle import KENNZAHLEN, STEUERFAELLE


def _zahl(v: Decimal | None) -> str:
    return "" if v is None else f"{v:.2f}".replace(".", ",")


def _datum(v: date | None) -> str:
    return v.strftime("%d.%m.%Y") if v else ""


def _csv(zeilen: list[list[str]]) -> str:
    puffer = io.StringIO()
    puffer.write("﻿")
    csv.writer(puffer, delimiter=";", lineterminator="\r\n").writerows(zeilen)
    return puffer.getvalue()


def belege_csv(bericht: Bericht) -> str:
    zeilen = [[
        "Rechnungsdatum", "Zahlungsdatum", "Art", "Partner", "USt-IdNr. Partner", "Rechnungsnummer",
        "Beschreibung", "Steuerfall", "Kennzahlen", "Währung", "Netto", "Steuersatz", "Steuer", "Kurs",
        "Kursquelle", "Netto EUR", "Steuer EUR", "Quelle", "Geprüft", "Fehler",
    ]]
    for z in bericht.zeilen:
        b = z.beleg
        info = STEUERFAELLE[b.steuerfall]
        zeilen.append([
            _datum(b.rechnungsdatum), _datum(b.zahlungsdatum),
            "Ausgang" if b.art.value == "ausgang" else "Eingang",
            b.partner, b.partner_ustid, b.rechnungsnummer, b.beschreibung, info.titel, info.kennzahlen,
            b.waehrung, _zahl(b.netto), _zahl(b.steuersatz), _zahl(b.steuer_in_belegwaehrung()),
            "" if z.kurs is None else str(z.kurs).replace(".", ","), z.kursquelle,
            _zahl(z.netto_eur), _zahl(z.steuer_eur), b.quelle_link or b.quelle,
            "nein" if b.pruefen else "ja", z.fehler,
        ])
    return _csv(zeilen)


def kennzahlen_csv(bericht: Bericht) -> str:
    zeilen = [["Zeitraum", "Kennzahl", "Bezeichnung", "Betrag", "Hinweis"]]
    for p in bericht.posten:
        k = KENNZAHLEN[p.kz]
        if p.kz.endswith("-steuer"):
            hinweis = "berechnet ELSTER"
        elif k.betrag_art == "bemessungsgrundlage":
            hinweis = "volle Euro"
        else:
            hinweis = ""
        zeilen.append([bericht.zeitraum, p.kz.split("-")[0], k.titel, _zahl(p.betrag), hinweis])
    zeilen.append([bericht.zeitraum, "", "Umsatzsteuer gesamt", _zahl(bericht.umsatzsteuer), ""])
    zeilen.append([bericht.zeitraum, "", "Vorsteuer gesamt", _zahl(bericht.vorsteuer), ""])
    if not bericht.vollstaendig:
        zeilen.append([bericht.zeitraum, "", "ACHTUNG: unvollständig, nicht alle Belege berücksichtigt", "", ""])
    return _csv(zeilen)
