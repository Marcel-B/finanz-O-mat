"""Bestand des ING-Direkt-Depots über FinTS (fiktive Daten, python-fints durch einen Nachbau ersetzt)."""

import argparse
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fints.client import NeedTANResponse
from fints.models import Holding

from ustva import depot
from ustva import kontoabruf as ka
from ustva.config import Config

JETZT = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
CFG = Config(fints_produkt_id="TESTPRODUKT", ing_fints_zugang="1234567890", ing_fints_pin="0000")
DEPOTNR = "5409876543"

BESTAND = [
    Holding(ISIN="IE00B4L5Y983", name="Beispiel MSCI World ETF", market_value=98.76, value_symbol="EUR",
            valuation_date=date(2026, 11, 7), pieces=25.5, total_value=2518.38, acquisitionprice=71.2),
    Holding(ISIN="DE0005190003", name="Muster Motoren AG", market_value=80.0, value_symbol="EUR",
            valuation_date=date(2026, 11, 10), pieces=10.0, total_value=None, acquisitionprice=None),
    Holding(ISIN=None, name="Kopfzeile ohne ISIN", market_value=None, value_symbol=None, valuation_date=None,
            pieces=None, total_value=None, acquisitionprice=None),
]


def tan(decoupled=True):
    return NeedTANResponse(None, SimpleNamespace(challenge="Bitte in der App freigeben"), decoupled=decoupled)


class FakeFints:
    """Was depot.IngFints von python-fints braucht."""

    def __init__(self, freigaben=0, decoupled=True, konten=None, bestand=BESTAND, fehler=None):
        self.freigaben = freigaben
        self.decoupled = decoupled
        self.konten = konten if konten is not None else [
            {"type": 1, "account_number": "1111111111", "iban": "DE00TEST", "product_name": "Girokonto"},
            {"type": 50, "account_number": DEPOTNR, "iban": None, "product_name": "Direkt-Depot ", "currency": "EUR"},
        ]
        self.bestand = bestand
        self.fehler = fehler
        self.verfahren = None
        self.nachfragen = 0
        self.init_tan_response = None
        self.abgefragt = []

    def get_current_tan_mechanism(self):
        return self.verfahren

    def fetch_tan_mechanisms(self):
        pass

    def get_tan_mechanisms(self):
        return {"999": None, "900": None}

    def set_tan_mechanism(self, code):
        self.verfahren = code

    def __enter__(self):
        if self.fehler:
            raise self.fehler
        self.init_tan_response = tan(self.decoupled) if self.freigaben else None

    def __exit__(self, *_):
        return False

    def send_tan(self, antwort, wert):
        assert wert == "" and antwort.decoupled
        self.nachfragen += 1
        self.freigaben -= 1
        return tan() if self.freigaben > 0 else None

    def get_information(self):
        return {"accounts": self.konten}

    def get_holdings(self, konto):
        self.abgefragt.append(konto)
        return self.bestand

    def deconstruct(self, including_private=False):
        assert including_private
        return b"zustand-neu"


class Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def schlafen(self, s):
        self.t += s


def zugang(fake, zustand=None):
    uhr = Uhr()
    gesehen = {}

    def fabrik(cfg, z):
        gesehen["zustand"] = z
        return fake

    z = depot.IngFints(CFG, zustand, client_fabrik=fabrik, schlafen=uhr.schlafen, uhr=uhr)
    z.gesehen = gesehen
    return z


def test_bestand(db):
    fake = FakeFints()
    a = depot.abrufen(db, CFG, zugang(fake, b"zustand-alt"), jetzt=JETZT)
    assert (a.ergebnis, a.fehler) == ("ok", "")
    (d,) = a.depots
    assert (d.nummer, d.name, d.schluessel) == (DEPOTNR, "Direkt-Depot", f"fints:50010517:{DEPOTNR}")
    assert fake.verfahren == "900"  # nicht das Einschrittverfahren
    assert fake.abgefragt[0].accountnumber == DEPOTNR and fake.abgefragt[0].blz == "50010517"
    etf, aktie = d.positionen
    assert (etf.stueck, etf.kurs, etf.wert, etf.einstand, etf.einstandswert) == (
        D("25.5"), D("98.76"), D("2518.38"), D("71.2"), D("1815.60"))
    assert aktie.wert == D("800.00")  # ohne Gesamtwert der Bank: Stück × Kurs
    assert d.wert == D("3318.38")

    (gespeichert,) = db.depots()
    assert (gespeichert["wert"], gespeichert["einstandswert"], gespeichert["stand"], gespeichert["positionen"]) == (
        "3318.38", "", "2026-11-10", 2)  # ohne Einstand der Aktie kein Einstandswert fürs Depot
    assert [p["isin"] for p in db.depot_positionen(d.schluessel)] == ["IE00B4L5Y983", "DE0005190003"]
    assert db.depot_werte(d.schluessel) == [{"tag": "2026-11-10", "wert": "3318.38"}]
    zustand = db.fints_zustand(depot.ZUGANG)
    assert (zustand["daten"], zustand["fehler"], zustand["freigabe"]) == (b"zustand-neu", "", False)

    # Ein zweiter Abruf ersetzt die Positionen
    fake2 = FakeFints(bestand=BESTAND[:1])
    z = zugang(fake2, zustand["daten"])
    depot.abrufen(db, CFG, z, jetzt=JETZT + timedelta(days=1))
    assert z.gesehen["zustand"] == b"zustand-neu"
    assert len(db.depot_positionen(d.schluessel)) == 1
    assert [w["tag"] for w in db.depot_werte(d.schluessel)] == ["2026-11-10", "2026-11-11"]
    assert db.depots()[0]["einstandswert"] == "1815.60"


def test_freigabe_in_der_app(db):
    fake = FakeFints(freigaben=3)
    hinweise = []
    a = depot.abrufen(db, CFG, zugang(fake), jetzt=JETZT, hinweis=hinweise.append)
    assert a.ergebnis == "ok" and fake.nachfragen == 3
    assert hinweise == ["Bitte den Zugriff in der ING-App freigeben: Bitte in der App freigeben"]


def test_freigabe_kommt_nicht(db):
    fake = FakeFints(freigaben=1000)
    a = depot.abrufen(db, CFG, zugang(fake), jetzt=JETZT, warten=timedelta(seconds=20))
    assert (a.ergebnis, a.freigabe) == ("freigabe", True)
    assert "nicht rechtzeitig" in a.fehler and not db.depots()
    assert db.fints_zustand(depot.ZUGANG)["freigabe"] is True

    # Im Hintergrund wird dann nicht wieder gefragt, bis ein Abruf von Hand durchkommt
    fake2 = FakeFints()
    a = depot.abrufen(db, CFG, zugang(fake2), jetzt=JETZT, hintergrund=True)
    assert a.ergebnis == "uebersprungen" and not fake2.abgefragt
    assert depot.abrufen(db, CFG, zugang(fake2), jetzt=JETZT).ergebnis == "ok"
    assert db.fints_zustand(depot.ZUGANG)["freigabe"] is False


def test_tan_statt_app(db):
    a = depot.abrufen(db, CFG, zugang(FakeFints(freigaben=1, decoupled=False)), jetzt=JETZT)
    assert a.ergebnis == "fehler" and "App-Freigabe" in a.fehler and not a.freigabe


def test_kein_depot_und_bankfehler(db):
    a = depot.abrufen(db, CFG, zugang(FakeFints(konten=[{"type": 1, "account_number": "1"}])), jetzt=JETZT)
    assert a.fehler == "Die ING meldet für diesen Zugang kein Depot (gemeldete Konten: Kontoart 1 „?“ …1)"
    a = depot.abrufen(db, CFG, zugang(FakeFints(fehler=ConnectionError("Bank antwortet nicht"))), jetzt=JETZT)
    assert a.fehler == "ConnectionError: Bank antwortet nicht"
    assert db.fints_zustand(depot.ZUGANG)["fehler"] == a.fehler


def test_nicht_eingerichtet(db):
    assert not depot.eingerichtet(Config())
    a = depot.abrufen(db, Config(), jetzt=JETZT)
    assert "FINTS_PRODUKT_ID" in a.fehler


def test_hintergrund(db, monkeypatch):
    from zoneinfo import ZoneInfo

    aufrufe = []

    def abrufen(db_, cfg, zugang=None, jetzt=None, hintergrund=False, **_):
        aufrufe.append(hintergrund)
        return depot.DepotAbruf()

    monkeypatch.setattr(depot, "abrufen", abrufen)
    h = ka.Hintergrundabruf(db, CFG, ["16:00"], tz=ZoneInfo("Europe/Berlin"))
    assert h.einmal(jetzt=datetime(2026, 11, 10, 15, 10, tzinfo=UTC)) is None  # keine Konten, aber das Depot
    assert aufrufe == [True]
    h.einmal(jetzt=datetime(2026, 11, 10, 15, 20, tzinfo=UTC))  # dieselbe Abrufzeit nicht zweimal
    assert aufrufe == [True]
    ka.Hintergrundabruf(db, Config(), ["16:00"], tz=ZoneInfo("Europe/Berlin")).einmal(
        jetzt=datetime(2026, 11, 10, 15, 10, tzinfo=UTC))
    assert aufrufe == [True]  # ohne Zugangsdaten kein Depotabruf


def test_api(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ustva import konten_web

    fake = FakeFints()
    app = FastAPI()
    app.include_router(konten_web.router(db, lambda: CFG, jetzt=lambda: JETZT,
                                         depot_fabrik=lambda cfg, z: zugang(fake, z)))
    client = TestClient(app)
    leer = client.get("/api/konten").json()
    assert leer["depot"] == {"eingerichtet": True, "fehler": "", "freigabe": False, "versucht": "", "abgelehnt": False}
    assert leer["depots"] == []

    antwort = client.post("/api/konten/depot/abrufen").json()
    schluessel = f"fints:50010517:{DEPOTNR}"
    assert antwort == {"fehler": "", "freigabe": False,
                       "depots": [{"schluessel": schluessel, "name": "Direkt-Depot", "positionen": 2}]}
    (d,) = client.get("/api/konten").json()["depots"]
    assert (d["wert"], d["positionen"], d["abgerufen"]) == ("3318.38", 2, "2026-11-10T15:00:00+00:00")
    bestand = client.get("/api/konten/depot", params={"schluessel": schluessel}).json()
    assert [p["name"] for p in bestand["positionen"]] == ["Beispiel MSCI World ETF", "Muster Motoren AG"]
    assert bestand["verlauf"] == [{"tag": "2026-11-10", "wert": "3318.38"}]
    assert client.get("/api/konten/depot", params={"schluessel": "fints:x"}).status_code == 404

    nicht = FastAPI()
    nicht.include_router(konten_web.router(db, lambda: Config()))
    assert TestClient(nicht).post("/api/konten/depot/abrufen").status_code == 400


def test_metriken_ohne_werte(db):
    from ustva import monitoring

    depot.abrufen(db, CFG, zugang(FakeFints()), jetzt=JETZT)
    text, _ = monitoring.metriken(monitoring.Bestand(db, None, None))
    text = text.decode()
    assert 'finanzomat_depot_positionen{bank="ING",depot="Direkt-Depot"} 2.0' in text
    assert "finanzomat_depot_freigabe_noetig 0.0" in text
    assert "3318" not in text and DEPOTNR not in text  # /metrics ist ohne Anmeldung erreichbar


def test_befehl(db, capsys):
    from ustva.__main__ import depot_befehl

    args = argparse.Namespace(schritt="depot", warten=120)
    assert depot_befehl(args, CFG, db, zugang(FakeFints(freigaben=1))) == 0
    out = capsys.readouterr().out
    assert "ING-App freigeben" in out and "Direkt-Depot …6543: 3.318,38 EUR, 2 Positionen" in out
    assert "IE00B4L5Y983" in out
    assert depot_befehl(args, Config(), db) == 1


@pytest.mark.parametrize("kontoart, depot_", [(50, True), (59, True), (1, False), (None, False)])
def test_depots_aus(kontoart, depot_):
    assert bool(depot.depots_aus({"accounts": [{"type": kontoart, "account_number": "1"}]})) is depot_


def test_depot_ohne_kontoart():
    """Manche Banken melden das Depot nicht als Kontoart 50–59; dann über HKWPD oder den Produktnamen."""
    from fints.client import FinTSOperations

    ueber_wpd = {"type": 1, "account_number": "2", "product_name": "Wertpapiere",
                 "supported_operations": {FinTSOperations.GET_HOLDINGS: True, FinTSOperations.GET_BALANCE: False}}
    ueber_name = {"type": None, "account_number": "3", "product_name": "Direkt-Depot"}
    giro = {"type": 1, "account_number": "1234567890", "product_name": "Girokonto",
            "supported_operations": {FinTSOperations.GET_HOLDINGS: False}}
    info = {"accounts": [ueber_wpd, ueber_name, giro]}
    assert depot.depots_aus(info) == [ueber_wpd, ueber_name]
    assert depot.konten_text({"accounts": [giro]}) == "Kontoart 1 „Girokonto“ …7890"
    assert depot.konten_text({"accounts": []}) == "keine"


class AbgelehnteAnmeldung(FakeFints):
    """Die ING lehnt die Anmeldung ab; python-fints wirft dann immer „PIN wrong?“."""

    def __init__(self, code="9942", text="PIN falsch", **kw):
        super().__init__(**kw)
        self.code, self.text = code, text
        self.anmeldungen = 0

    def _process_response(self, dialog, segment, antwort):
        pass

    def fetch_tan_mechanisms(self):
        from fints.exceptions import FinTSClientPINError

        self.anmeldungen += 1
        self._process_response(None, None, SimpleNamespace(code="3920", text="Zugelassene Verfahren"))
        self._process_response(None, None, SimpleNamespace(code="9800", text="Dialog abgebrochen"))
        self._process_response(None, None, SimpleNamespace(code=self.code, text=self.text))
        raise FinTSClientPINError("Error during dialog initialization, PIN wrong?")


def test_anmeldung_abgelehnt(db):
    fake = AbgelehnteAnmeldung()
    a = depot.abrufen(db, CFG, zugang(fake), jetzt=JETZT)
    assert a.ergebnis == "fehler"
    assert "9800 Dialog abgebrochen; 9942 PIN falsch" in a.fehler and "3920" not in a.fehler
    assert "PIN wrong" not in a.fehler
    stand = db.fints_zustand(depot.ZUGANG)
    assert stand["abgelehnt"] is True and stand["fehler"] == a.fehler

    # Automatisch nicht noch einmal, sonst sperrt die ING nach drei falschen PINs
    a = depot.abrufen(db, CFG, zugang(fake), jetzt=JETZT, hintergrund=True)
    assert "abgelehnt" in a.uebersprungen and fake.anmeldungen == 1

    # Von Hand wird es versucht; klappt es, läuft der Hintergrund wieder
    assert depot.abrufen(db, CFG, zugang(FakeFints()), jetzt=JETZT).ergebnis == "ok"
    assert db.fints_zustand(depot.ZUGANG)["abgelehnt"] is False
    assert depot.abrufen(db, CFG, zugang(FakeFints()), jetzt=JETZT, hintergrund=True).ergebnis == "ok"


def test_anmeldung_ohne_text_der_bank(db):
    from fints.exceptions import FinTSClientTemporaryAuthError

    class Gesperrt(FakeFints):
        def fetch_tan_mechanisms(self):
            raise FinTSClientTemporaryAuthError("Account is temporarily locked.")

    a = depot.abrufen(db, CFG, zugang(Gesperrt()), jetzt=JETZT)
    assert "(FinTSClientTemporaryAuthError)" in a.fehler and db.fints_zustand(depot.ZUGANG)["abgelehnt"]
