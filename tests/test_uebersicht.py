from datetime import date
from decimal import Decimal

from ustva.models import Beleg
from ustva.steuerfaelle import Art, Steuerfall
from ustva.uebersicht import jahresuebersicht, uebersicht_csv


def _belege() -> list[Beleg]:
    return [
        # Ist-Versteuerung: zählt im Oktober (Zahlungseingang), nicht im September
        Beleg(Art.AUSGANG, Steuerfall.INLAND, date(2026, 9, 25), Decimal("1000"),
              zahlungsdatum=date(2026, 10, 2), partner="Kunde GmbH"),
        Beleg(Art.AUSGANG, Steuerfall.EU_LEISTUNG, date(2026, 9, 30), Decimal("500"), Decimal("0"),
              partner="Kunde BV"),
        Beleg(Art.EINGANG, Steuerfall.VORSTEUER_INLAND, date(2026, 9, 12), Decimal("50"), partner="Hosting GmbH"),
        Beleg(Art.EINGANG, Steuerfall.AUSLAND_13B, date(2026, 9, 3), Decimal("20"), waehrung="USD",
              partner="OpenAI, LLC"),
        Beleg(Art.EINGANG, Steuerfall.IGNORIEREN, date(2026, 9, 4), Decimal("999"), partner="Privat"),
        Beleg(Art.EINGANG, Steuerfall.VORSTEUER_INLAND, date(2025, 12, 1), Decimal("70"), partner="Vorjahr"),
    ]


def test_jahresuebersicht_ist(umrechner):
    d = jahresuebersicht(_belege(), 2026, umrechner, "ist")
    sep, okt = d["monate"][8], d["monate"][9]

    assert (sep["monat"], sep["einnahmen"], sep["belege"]) == ("September", "500.00", 3)
    assert sep["ausgaben"] == "67.24"  # 50 + 20 USD / 1,16
    assert okt["einnahmen"] == "1000.00"
    assert d["summe"]["einnahmen"] == "1500.00"
    assert d["summe"]["ergebnis"] == "1432.76"
    assert d["kunden"][0] == {"name": "Kunde GmbH", "betrag": "1000.00"}
    assert [x["name"] for x in d["lieferanten"]] == ["Hosting GmbH", "OpenAI, LLC"]
    assert d["unvollstaendig"] == 0


def test_jahresuebersicht_soll_und_fehlender_kurs(umrechner):
    belege = _belege() + [Beleg(Art.EINGANG, Steuerfall.AUSLAND_13B, date(2026, 3, 1), Decimal("5"), waehrung="GBP")]
    d = jahresuebersicht(belege, 2026, umrechner, "soll")
    assert d["monate"][8]["einnahmen"] == "1500.00"
    assert d["monate"][9]["einnahmen"] == "0.00"
    assert d["unvollstaendig"] == 1


def test_uebersicht_csv(umrechner):
    text = uebersicht_csv(jahresuebersicht(_belege(), 2026, umrechner, "ist"))
    zeilen = text.lstrip("﻿").splitlines()
    assert zeilen[0].startswith("Monat;Einnahmen netto")
    assert len(zeilen) == 14
    assert zeilen[9] == "2026-09;500,00;67,24;432,76;-9,50;3"
    assert zeilen[-1].startswith("2026;1500,00;67,24;1432,76;")
