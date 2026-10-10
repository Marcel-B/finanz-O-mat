from datetime import date
from decimal import Decimal as D

from ustva.berechnung import berechnen
from ustva.doppelt import doppel, gleich
from ustva.models import Beleg
from ustva.steuerfaelle import Art, Steuerfall


def beleg(**felder) -> Beleg:
    werte = {"art": Art.EINGANG, "steuerfall": Steuerfall.VORSTEUER_INLAND, "rechnungsdatum": date(2026, 9, 22),
             "netto": D("52.85"), "steuer": D("10.04"), "partner": "Telekom Deutschland GmbH"}
    return Beleg(**(werte | felder))


def test_gleiche_rechnungsnummer():
    assert gleich(beleg(rechnungsnummer="735 265 0522"), beleg(rechnungsnummer="7352650522", partner=""))
    assert not gleich(beleg(rechnungsnummer="735 265 0522"), beleg(rechnungsnummer="735 265 0523"))


def test_ohne_nummer_zaehlt_der_partner():
    assert gleich(beleg(), beleg(partner="Telekom", rechnungsnummer="735 265 0522"))
    assert not gleich(beleg(), beleg(partner="Hetzner Online GmbH"))
    assert not gleich(beleg(), beleg(partner=""))


def test_datum_betrag_und_art_muessen_passen():
    assert not gleich(beleg(), beleg(rechnungsdatum=date(2026, 8, 22)))
    assert not gleich(beleg(), beleg(netto=D("52.86")))
    assert not gleich(beleg(), beleg(art=Art.AUSGANG, steuerfall=Steuerfall.INLAND))
    assert not gleich(beleg(netto=D(0), steuer=D(0)), beleg(netto=D(0), steuer=D(0)))  # nichts erkannt


def test_posten_derselben_e_rechnung_sind_kein_duplikat():
    a = beleg(id=1, quelle="paperless", quelle_ref="40")
    assert doppel(beleg(id=2, quelle="paperless", quelle_ref="40#2"), [a]) is None
    assert doppel(beleg(id=3, quelle="paperless", quelle_ref="41"), [a]) is a


def test_hinweis_in_der_voranmeldung(umrechner):
    belege = [beleg(id=1, quelle="paperless", quelle_ref="40"), beleg(id=2, quelle="paperless", quelle_ref="41"),
              beleg(id=3, partner="Hetzner Online GmbH")]
    texte = [h.text for h in berechnen(belege, 2026, 9, umrechner, "ist").hinweise if h.stufe == "warnung"]
    assert [t for t in texte if t.startswith("Doppelt erfasst?")] == [
        "Doppelt erfasst? Telekom Deutschland GmbH vom 22.09.2026 über 62,89 EUR (Paperless-Dokument 40 und "
        "Paperless-Dokument 41). Ist es dieselbe Rechnung, einen der Belege löschen, sonst zählt die Steuer doppelt."
    ]
