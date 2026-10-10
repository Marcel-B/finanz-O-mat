from datetime import date
from decimal import Decimal as D

from ustva.berechnung import berechnen
from ustva.models import Beleg
from ustva.steuerfaelle import Art, Steuerfall

SEP = date(2026, 9, 15)


def ausgang(netto, fall=Steuerfall.INLAND, satz="19", **kw):
    return Beleg(art=Art.AUSGANG, steuerfall=fall, rechnungsdatum=kw.pop("datum", SEP), netto=D(netto),
                 steuersatz=D(satz), **kw)


def eingang(netto, fall=Steuerfall.VORSTEUER_INLAND, satz="19", **kw):
    return Beleg(art=Art.EINGANG, steuerfall=fall, rechnungsdatum=kw.pop("datum", SEP), netto=D(netto),
                 steuersatz=D(satz), **kw)


def september_belege():
    return [
        ausgang("3800.00"),
        ausgang("1234.56"),
        ausgang("100.50", satz="7"),
        ausgang("8400.00", Steuerfall.EU_LEISTUNG, "0", partner_ustid="NL853746333B01"),
        ausgang("1000.00", Steuerfall.DRITTLAND_LEISTUNG, "0", waehrung="USD"),
        eingang("50.00", steuer=D("9.50")),
        eingang("200.00"),
        eingang("20.00", Steuerfall.AUSLAND_13B, waehrung="USD", partner="OpenAI"),
        eingang("100.00", Steuerfall.EU_LEISTUNG_13B, partner_ustid="IE9825613N"),
        eingang("999.00", Steuerfall.IGNORIEREN),
        ausgang("5000.00", datum=date(2026, 10, 1)),  # anderer Monat
    ]


def test_kennzahlen_september(umrechner):
    b = berechnen(september_belege(), 2026, 9, umrechner)
    assert b.wert("81") == D("5034")  # volle Euro, abgerundet
    assert b.wert("81-steuer") == D("956.46")
    assert b.wert("86") == D("100")
    assert b.wert("86-steuer") == D("7.00")
    assert b.wert("21") == D("8400")
    assert b.wert("45") == D("862")  # 1000 USD / 1,16
    assert b.wert("66") == D("47.50")
    assert b.wert("84") == D("17")  # 20 USD / 1,16 = 17,24 EUR
    assert b.wert("85") == D("3.28")
    assert b.wert("46") == D("100")
    assert b.wert("47") == D("19.00")
    assert b.wert("67") == D("22.28")
    assert b.umsatzsteuer == D("985.74")
    assert b.vorsteuer == D("69.78")
    assert b.wert("83") == b.zahllast == D("915.96")
    assert b.vollstaendig
    assert len(b.zeilen) == 10  # Oktober-Beleg nicht enthalten, ignorierter Beleg schon (zur Anzeige)
    assert b.zm == [{"ustid": "NL853746333B01", "art": "Sonstige Leistung", "betrag": "8400"}]
    texte = " ".join(h.text for h in b.hinweise)
    assert "Zusammenfassenden Meldung" in texte
    assert "Reverse Charge" in texte
    assert "keine Steuerberatung" in texte


def test_reihenfolge_wie_formular(umrechner):
    b = berechnen(september_belege(), 2026, 9, umrechner)
    assert [p.kz for p in b.posten] == [
        "81", "81-steuer", "86", "86-steuer", "21", "45", "46", "47", "84", "85", "66", "67", "83",
    ]


def test_leerer_monat_ist_nullmeldung(umrechner):
    b = berechnen([], 2026, 9, umrechner)
    assert [p.kz for p in b.posten] == ["83"]
    assert b.zahllast == 0


def test_ueberschuss_bei_hoher_vorsteuer(umrechner):
    b = berechnen([eingang("1000.00")], 2026, 9, umrechner)
    assert b.zahllast == D("-190.00")
    assert any("erstattet" in h.text for h in b.hinweise)


def test_fehlender_kurs_macht_bericht_unvollstaendig(umrechner):
    b = berechnen([eingang("10.00", Steuerfall.AUSLAND_13B, waehrung="GBP"), ausgang("100")], 2026, 9, umrechner)
    assert not b.vollstaendig
    assert b.wert("84") == 0
    assert b.wert("81") == D("100")
    assert "GBP" in b.zeilen[0].fehler


def test_kurs_am_beleg_hat_vorrang(umrechner):
    b = berechnen([eingang("20.00", Steuerfall.AUSLAND_13B, waehrung="USD", kurs=D("1.25"))], 2026, 9, umrechner)
    assert b.zeilen[0].netto_eur == D("16.00")
    assert b.wert("85") == D("3.04")


def test_ist_versteuerung_nach_zahlungseingang(umrechner):
    belege = [
        ausgang("1000", datum=date(2026, 8, 20), zahlungsdatum=date(2026, 9, 3)),  # zählt im September
        ausgang("500", datum=date(2026, 9, 10)),  # noch nicht bezahlt
        eingang("100", datum=date(2026, 9, 5)),  # Vorsteuer nach Rechnungsdatum
    ]
    ist = berechnen(belege, 2026, 9, umrechner, "ist")
    assert ist.wert("81") == D("1000")
    assert ist.wert("66") == D("19.00")
    soll = berechnen(belege, 2026, 9, umrechner, "soll")
    assert soll.wert("81") == D("500")


def test_ungepruefte_belege_werden_gemeldet(umrechner):
    b = berechnen([ausgang("100", pruefen=True)], 2026, 9, umrechner)
    assert any("nicht geprüft" in h.text for h in b.hinweise)


def test_eu_kunde_ohne_ustid(umrechner):
    b = berechnen([ausgang("100", Steuerfall.EU_LEISTUNG, "0")], 2026, 9, umrechner)
    assert any("USt-IdNr" in h.text and h.stufe == "warnung" for h in b.hinweise)


def test_innergemeinschaftlicher_erwerb(umrechner):
    b = berechnen([eingang("300.40", Steuerfall.EU_ERWERB)], 2026, 9, umrechner)
    assert b.wert("89") == D("300")
    assert b.wert("89-steuer") == D("57.00")
    assert b.wert("61") == D("57.00")
    assert b.zahllast == 0
