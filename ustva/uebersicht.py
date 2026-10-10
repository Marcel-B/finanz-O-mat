"""Grobe Jahresübersicht der Ein- und Ausgaben zum eigenen Überblick.

Keine Gewinnermittlung fürs Finanzamt: Die Beträge sind netto (ohne Umsatzsteuer)
und folgen denselben Zeiträumen wie die Voranmeldung, bei Ist-Versteuerung also
dem Zahlungseingang der Ausgangsrechnungen.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from decimal import Decimal

from .berechnung import BelegZeile, berechnen
from .fristen import MONATSNAMEN
from .models import Beleg
from .steuerfaelle import Art, Steuerfall
from .waehrung import Umrechner

NULL = Decimal(0)


def _geld(betrag: Decimal) -> str:
    return f"{betrag:.2f}"


def _name(z: BelegZeile) -> str:
    b = z.beleg
    return b.partner.strip() or b.beschreibung.strip() or "ohne Namen"


def _top(summen: dict[str, Decimal], anzahl: int = 8) -> list[dict]:
    reihe = sorted(summen.items(), key=lambda x: x[1], reverse=True)
    return [{"name": name, "betrag": _geld(betrag)} for name, betrag in reihe[:anzahl]]


def jahresuebersicht(belege: list[Beleg], jahr: int, umrechner: Umrechner, versteuerung: str) -> dict:
    monate = []
    nach_kunde: dict[str, Decimal] = defaultdict(lambda: NULL)
    nach_lieferant: dict[str, Decimal] = defaultdict(lambda: NULL)
    summe = {"einnahmen": NULL, "ausgaben": NULL, "zahllast": NULL}
    unvollstaendig = 0

    for monat in range(1, 13):
        bericht = berechnen(belege, jahr, monat, umrechner, versteuerung)
        einnahmen = ausgaben = NULL
        anzahl = 0
        for z in bericht.zeilen:
            if z.beleg.steuerfall == Steuerfall.IGNORIEREN or z.netto_eur is None:
                continue
            anzahl += 1
            if z.beleg.art == Art.AUSGANG:
                einnahmen += z.netto_eur
                nach_kunde[_name(z)] += z.netto_eur
            else:
                ausgaben += z.netto_eur
                nach_lieferant[_name(z)] += z.netto_eur
        fehlerhaft = sum(1 for z in bericht.zeilen if z.fehler)
        unvollstaendig += fehlerhaft
        monate.append({
            "zeitraum": bericht.zeitraum,
            "monat": MONATSNAMEN[monat - 1],
            "einnahmen": _geld(einnahmen),
            "ausgaben": _geld(ausgaben),
            "ergebnis": _geld(einnahmen - ausgaben),
            "zahllast": _geld(bericht.zahllast),
            "belege": anzahl,
            "fehlerhaft": fehlerhaft,
        })
        summe["einnahmen"] += einnahmen
        summe["ausgaben"] += ausgaben
        summe["zahllast"] += bericht.zahllast

    return {
        "jahr": jahr,
        "versteuerung": versteuerung,
        "monate": monate,
        "summe": {
            "einnahmen": _geld(summe["einnahmen"]),
            "ausgaben": _geld(summe["ausgaben"]),
            "ergebnis": _geld(summe["einnahmen"] - summe["ausgaben"]),
            "zahllast": _geld(summe["zahllast"]),
        },
        "kunden": _top(nach_kunde),
        "lieferanten": _top(nach_lieferant),
        "unvollstaendig": unvollstaendig,
    }


def _zahl(text: str) -> str:
    return f"{Decimal(text):.2f}".replace(".", ",")


def uebersicht_csv(daten: dict) -> str:
    puffer = io.StringIO()
    puffer.write("﻿")
    w = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    w.writerow(["Monat", "Einnahmen netto", "Ausgaben netto", "Ergebnis", "USt-Zahllast", "Belege"])
    for m in daten["monate"]:
        w.writerow([m["zeitraum"], _zahl(m["einnahmen"]), _zahl(m["ausgaben"]), _zahl(m["ergebnis"]),
                    _zahl(m["zahllast"]), m["belege"]])
    s = daten["summe"]
    w.writerow([str(daten["jahr"]), _zahl(s["einnahmen"]), _zahl(s["ausgaben"]), _zahl(s["ergebnis"]),
                _zahl(s["zahllast"]), sum(m["belege"] for m in daten["monate"])])
    return puffer.getvalue()
