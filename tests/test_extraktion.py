from datetime import date
from decimal import Decimal as D

import pytest

from ustva.extraktion import betrag_parsen, daten_finden, erkennen, ustids_finden
from ustva.steuerfaelle import Art, Steuerfall

from . import beispiele


@pytest.mark.parametrize("text,wert", [
    ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("59,50", "59.50"), ("20.00", "20.00"),
    ("8.400", "8400"), ("1234", "1234"), ("-12,30", "-12.30"), ("1'234.50", "1234.50"),
])
def test_betrag_parsen(text, wert):
    assert betrag_parsen(text) == D(wert)


def test_daten_finden():
    assert daten_finden("Rechnungsdatum: 12.09.2026") == [date(2026, 9, 12)]
    assert daten_finden("Date of issue September 3, 2026") == [date(2026, 9, 3)]
    assert daten_finden("Datum 3. Oktober 2026") == [date(2026, 10, 3)]
    assert daten_finden("2026-09-05") == [date(2026, 9, 5)]


def test_ustids():
    assert ustids_finden("VAT NL853746333B01, USt-IdNr. DE123456789", ausser="DE123456789") == ["NL853746333B01"]
    assert ustids_finden("Date of issue September 3") == []


def test_openai_drittland_reverse_charge():
    e = erkennen(beispiele.OPENAI, Art.EINGANG, "DE123456789")
    assert e.steuerfall == Steuerfall.AUSLAND_13B
    assert e.waehrung == "USD"
    assert e.netto == D("20.00")
    assert e.rechnungsdatum == date(2026, 9, 3)
    assert e.rechnungsnummer == "8F3A21-0007"


def test_deutsche_rechnung_mit_vorsteuer():
    e = erkennen(beispiele.HOSTING_DE, Art.EINGANG, "DE123456789")
    assert e.steuerfall == Steuerfall.VORSTEUER_INLAND
    assert (e.netto, e.steuer, e.brutto, e.steuersatz) == (D("50.00"), D("9.50"), D("59.50"), D("19"))
    assert e.rechnungsdatum == date(2026, 9, 12)


def test_ausgangsrechnung_eu():
    e = erkennen(beispiele.AUSGANG_EU, Art.AUSGANG, "DE123456789")
    assert e.steuerfall == Steuerfall.EU_LEISTUNG
    assert e.partner_ustid == "NL853746333B01"
    assert e.netto == D("8400.00")


def test_ausgangsrechnung_inland():
    e = erkennen(beispiele.AUSGANG_INLAND, Art.AUSGANG)
    assert e.steuerfall == Steuerfall.INLAND
    assert (e.netto, e.steuer, e.steuersatz) == (D("3800.00"), D("722.00"), D("19"))


def test_eu_anbieter_reverse_charge():
    e = erkennen(beispiele.EU_SAAS, Art.EINGANG, "DE123456789")
    assert e.steuerfall == Steuerfall.EU_LEISTUNG_13B
    assert e.partner_ustid == "IE9825613N"
    assert e.rechnungsdatum == date(2026, 9, 5)


def test_nur_bruttobetrag():
    e = erkennen("Rechnung\nDatum 01.09.2026\nGesamtbetrag 119,00 €\nenthaltene MwSt 19 %", Art.EINGANG)
    assert e.netto == D("100.00")
    assert e.steuersatz == D("19")


def test_leerer_text():
    e = erkennen("   ", Art.EINGANG)
    assert e.netto is None
    assert "Kein Text" in e.hinweise[0]
