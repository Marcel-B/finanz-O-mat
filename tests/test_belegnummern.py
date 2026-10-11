from datetime import date
from decimal import Decimal as D

import httpx
import pytest
from fastapi.testclient import TestClient

from ustva import bank, belegnummern
from ustva.config import Config
from ustva.extraktion import erkennen
from ustva.models import Beleg, Umsatz
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app

# So steht es im Kontoauszug (N26) und auf der Rechnung der Telekom (fiktive Nummern).
ZWECK = "Festnetz Vertragskonto 1234567890 RG 9876543210/17.12.2025"
TEXT_DEZEMBER = """Telekom Deutschland GmbH, 53171 Bonn
Datum 17.12.2025
Rechnungsnummer 987 654 3210
Kundennummer 111 222 3334
12345 Musterstadt
Buchungskonto 123 456 7890
Rechnungsbetrag 62,89 €"""
TEXT_NOVEMBER = TEXT_DEZEMBER.replace("987 654 3210", "987 600 1111").replace("17.12.2025", "19.11.2025")


@pytest.fixture(autouse=True)
def keine_pause():
    belegnummern._pause_bis = 0.0


def telekom(datum: date, netto: str, **felder) -> Beleg:
    return Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=datum, netto=D(netto),
                 partner="Telekom Deutschland GmbH", **felder)


def umsatz(zweck: str = ZWECK, betrag: str = "-62.89") -> Umsatz:
    return Umsatz(konto="N26", buchungsdatum=date(2026, 1, 2), betrag=D(betrag),
                  partner="Telekom Deutschland GmbH", verwendungszweck=zweck)


def test_nummern_ueber_gruppen_hinweg():
    n = belegnummern.nummern(TEXT_DEZEMBER)
    assert {"9876543210", "1234567890", "1112223334"} <= n
    assert "111222333412345" not in n  # nicht über Zeilen hinweg
    assert not {x for x in n if len(x) < 6}  # PLZ, Jahre, Beträge nicht
    assert "2025123456789012" in belegnummern.nummern("Rechnung 2025-123456789012")


def test_nummern_im_zweck():
    assert belegnummern.nummern_im_zweck(umsatz()) == ["1234567890", "9876543210"]
    assert belegnummern.nummern_im_zweck(umsatz("Rechnung 2026-030")) == []


def test_treffer_unterscheidet_rechnungs_und_kundennummer():
    dez, nov, mobil = (telekom(date(2025, 12, 17), "52.85", id=1), telekom(date(2025, 11, 19), "52.85", id=2),
                       telekom(date(2025, 12, 2), "54.54", id=3))
    aus_text = {1: belegnummern.nummern(TEXT_DEZEMBER), 2: belegnummern.nummern(TEXT_NOVEMBER), 3: set()}
    t = belegnummern.treffer(umsatz(), [dez, nov, mobil], aus_text)
    assert [x.to_dict() for x in t[1]] == [{"nummer": "1234567890", "eindeutig": False},
                                           {"nummer": "9876543210", "eindeutig": True}]
    assert [x.to_dict() for x in t[2]] == [{"nummer": "1234567890", "eindeutig": False}]
    assert 3 not in t


def test_steuerposten_einer_rechnung_zaehlen_als_ein_dokument():
    a = telekom(date(2025, 12, 17), "40", id=1, quelle="paperless", quelle_ref="21")
    b = telekom(date(2025, 12, 17), "10", id=2, quelle="paperless", quelle_ref="21#2")
    nummern = belegnummern.nummern(TEXT_DEZEMBER)
    t = belegnummern.treffer(umsatz(), [a, b], {1: nummern, 2: nummern})
    assert all(x.eindeutig for x in t[1] + t[2])


def test_nachtragen_aus_paperless(db):
    anfragen = []

    def handler(request: httpx.Request) -> httpx.Response:
        anfragen.append(request)
        return httpx.Response(200, json={"count": 1, "next": None,
                                         "results": [{"id": 21, "content": TEXT_DEZEMBER}]})

    pl = db.beleg_anlegen(telekom(date(2025, 12, 17), "52.85", quelle="paperless", quelle_ref="21"))
    weg = db.beleg_anlegen(telekom(date(2025, 11, 19), "52.85", quelle="paperless", quelle_ref="9"))
    manuell = db.beleg_anlegen(telekom(date(2025, 10, 19), "52.85"))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="geheim")
    client = httpx.Client(transport=httpx.MockTransport(handler))

    belegnummern.nachtragen(db, cfg, db.alle_belege(), client)
    [anfrage] = anfragen
    assert anfrage.url.params["id__in"] == "9,21"
    assert anfrage.url.params["fields"] == "id,content"
    gespeichert = db.beleg_nummern()
    assert "9876543210" in gespeichert[pl.id]
    assert gespeichert[weg.id] == set() == gespeichert[manuell.id]  # gelöscht bzw. ohne Dokument

    belegnummern.nachtragen(db, cfg, db.alle_belege(), client)
    assert len(anfragen) == 1  # schon bekannt

    db.beleg_nummern_vergessen(pl.id)
    db.beleg_loeschen(weg.id)
    assert set(db.beleg_nummern()) == {manuell.id}


def test_paperless_nicht_erreichbar(db):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")

    db.beleg_anlegen(telekom(date(2025, 12, 17), "52.85", quelle="paperless", quelle_ref="21"))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="geheim")
    belegnummern.nachtragen(db, cfg, db.alle_belege(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert db.beleg_nummern() == {}  # später noch einmal versuchen
    assert belegnummern._pause_bis > 0


def test_nachtragen_aus_verzeichnis(db, tmp_path):
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / "telekom.txt").write_text(TEXT_DEZEMBER, encoding="utf-8")
    b = db.beleg_anlegen(telekom(date(2025, 12, 17), "52.85", quelle="verzeichnis", quelle_ref="abc",
                                 quelle_link="eingang/telekom.txt"))
    belegnummern.nachtragen(db, Config(rechnungs_verzeichnis=tmp_path), db.alle_belege())
    assert "1234567890" in db.beleg_nummern()[b.id]


def test_kandidaten_mit_rechnungsnummer_zuerst(db, umrechner):
    client = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))
    db.umsatz_anlegen(umsatz())
    [u] = db.alle_umsaetze()
    passt_betrag = db.beleg_anlegen(telekom(date(2026, 9, 22), "52.85"))  # gleicher Betrag, falscher Monat
    dez = db.beleg_anlegen(telekom(date(2025, 12, 17), "42.81"))  # Betrag falsch erkannt
    nov = db.beleg_anlegen(telekom(date(2025, 11, 19), "42.81"))
    db.beleg_nummern_speichern({dez.id: " ".join(belegnummern.nummern(TEXT_DEZEMBER)),
                                nov.id: " ".join(belegnummern.nummern(TEXT_NOVEMBER))})

    kandidaten = client.get(f"/api/umsaetze/{u.id}/kandidaten").json()
    assert [k["id"] for k in kandidaten] == [dez.id, passt_betrag.id, nov.id]
    assert kandidaten[0]["nummern"] == [{"nummer": "1234567890", "eindeutig": False},
                                        {"nummer": "9876543210", "eindeutig": True}]
    assert "nummern" not in kandidaten[1]

    # Nach einer Nummer aus dem Dokumenttext lässt sich auch suchen.
    [k] = client.get(f"/api/umsaetze/{u.id}/kandidaten", params={"suche": "9876543210"}).json()
    assert k["id"] == dez.id

    # Hängt der Beleg schon an einem anderen Umsatz, taucht er trotzdem auf.
    db.umsatz_anlegen(Umsatz(konto="N26", buchungsdatum=date(2025, 12, 30), betrag=D("-50.94"),
                             partner="Telekom", verwendungszweck="", pruefsumme="x"))
    anderer = next(x for x in db.alle_umsaetze() if x.id != u.id)
    bank.zuordnen(db, anderer, dez)
    kandidaten = client.get(f"/api/umsaetze/{u.id}/kandidaten").json()
    assert kandidaten[-1]["id"] == dez.id and kandidaten[-1]["zugeordnet"] == "2025-12-30"


def test_rechnungsnummer_in_gruppen():
    assert erkennen(TEXT_DEZEMBER, Art.EINGANG).rechnungsnummer == "987 654 3210"
    assert erkennen("Rechnungsnummer 12 3456 7890 1234\nDatum", Art.EINGANG).rechnungsnummer == "12 3456 7890 1234"
    assert erkennen("Rechnungsnr.: RE-1036 vom 05.12.2025", Art.AUSGANG).rechnungsnummer == "RE-1036"
    assert erkennen("Rechnungsnummer 1234 12,50 EUR", Art.AUSGANG).rechnungsnummer == "1234"


def test_abgleich_findet_rechnungsnummer_mit_leerzeichen():
    b = telekom(date(2025, 12, 17), "52.85", rechnungsnummer="987 654 3210")
    assert bank.kandidaten(umsatz(), [b], set()) == [b]
