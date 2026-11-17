"""Reiter „Konten“: eigene Namen, Farben und Reihenfolge, Summe der vorgemerkten Umsätze, Verlauf von Kontostand und
Depotwert."""

from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ustva import depot, konten_web, kontoverlauf, monitoring
from ustva.config import Config
from ustva.db import stichtag
from ustva.models import BankKonto, Umsatz

from . import metriken

# Fiktive Konten
GIRO = "DE89370400440532013000"
EXTRA = "DE75512108001245126199"
JETZT = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)


def konto(schluessel=GIRO, **mehr):
    werte = dict(uid="u", sitzung="s", bank="ING", land="DE", konto="ING", iban=schluessel,
                 bezeichnung="Extra-Konto", gueltig_bis="2027-02-08T08:00:00+00:00")
    return BankKonto(schluessel=schluessel, **(werte | mehr))


def buchung(tag, betrag, nr):
    return Umsatz(buchungsdatum=date.fromisoformat(tag), betrag=D(betrag), partner=f"Partner {nr}",
                  pruefsumme=f"p{nr}")


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(konten_web.router(db, Config, jetzt=lambda: JETZT))
    return TestClient(app)


def tage(verlauf):
    return {t.isoformat(): str(w) for t, w in verlauf}


def test_verlauf_aus_umsaetzen():
    buchungen = [(date(2026, 11, 10), D("-20.00")), (date(2026, 11, 12), D("100.00")),
                 (date(2026, 11, 12), D("-30.00"))]
    v = kontoverlauf.konto(D("500.00"), date(2026, 11, 13), buchungen, {}, date(2026, 11, 14))
    assert tage(v) == {
        "2026-11-09": "450.00",  # Anfangsbestand vor der ersten Buchung
        "2026-11-10": "430.00", "2026-11-11": "430.00", "2026-11-12": "500.00", "2026-11-13": "500.00",
        "2026-11-14": "500.00",  # nach dem Stichtag bis heute fortgeschrieben
    }
    # Ein gemerkter Stand gilt (z. B. fehlende Umsätze vor dem ersten Auszug) und rechnet von dort weiter zurück
    v = kontoverlauf.konto(D("500.00"), date(2026, 11, 13), buchungen, {date(2026, 11, 11): D("470.00")},
                           date(2026, 11, 13))
    assert tage(v)["2026-11-11"] == "470.00" and tage(v)["2026-11-09"] == "490.00"


def test_verlauf_nach_dem_stichtag_mit_den_buchungen_weiter():
    v = kontoverlauf.konto(D("100"), date(2026, 11, 12), [(date(2026, 11, 13), D("-5"))], {}, date(2026, 11, 14))
    assert tage(v)["2026-11-14"] == "95"


def test_verlauf_ohne_saldo():
    assert kontoverlauf.konto(None, None, [(date(2026, 11, 1), D("1"))], {}, date(2026, 11, 14)) == []
    v = kontoverlauf.konto(None, None, [], {date(2026, 11, 13): D("7")}, date(2026, 11, 14))
    assert tage(v) == {"2026-11-13": "7", "2026-11-14": "7"}


def test_summe_mehrerer_konten():
    a = [(date(2026, 11, 1), D("10")), (date(2026, 11, 2), D("20")), (date(2026, 11, 3), D("30"))]
    b = [(date(2026, 11, 2), D("5")), (date(2026, 11, 3), D("6"))]
    # b zählt am 1. mit seinem ersten Stand, nicht mit null
    assert tage(kontoverlauf.summe([a, b])) == {"2026-11-01": "15", "2026-11-02": "25", "2026-11-03": "36"}
    assert kontoverlauf.summe([[], []]) == []


def test_stichtag_in_deutscher_zeit():
    assert stichtag("2026-11-13") == "2026-11-13"
    assert stichtag("2026-11-13T23:30:00+00:00") == "2026-11-14"  # 0:30 Uhr in Berlin
    assert stichtag("2026-11-13T10:00:00") == "2026-11-13"


def test_kontostand_wird_gemerkt(db):
    db.bank_konto_speichern(konto(saldo="100.00", saldo_stand="2026-11-12"))
    db.bank_konto_speichern(konto(saldo="120.00", saldo_stand="2026-11-13T08:00:00+00:00"))
    db.bank_konto_speichern(konto(saldo="125.00", saldo_stand="2026-11-13T18:00:00+00:00"))  # letzter des Tages
    db.bank_konto_fints(GIRO, "2026-11-14T09:00:00+00:00", "130.00", "EUR", "2026-11-14T09:00:00+00:00")
    assert db.konto_salden(GIRO) == {"2026-11-12": "100.00", "2026-11-13": "125.00", "2026-11-14": "130.00"}
    db.bank_konto_loeschen(GIRO)
    assert db.konto_salden(GIRO) == {}


def test_eigene_namen(db, client):
    db.bank_konto_speichern(konto())
    db.bank_konto_speichern(konto(EXTRA))
    db.depot_speichern(depot.Depot(nummer="1234567890", positionen=[
        depot.Position("DE0000000001", "Musterfonds", D("2"), D("50"), wert=D("100.00"))]), "2026-11-14T09:00:00")
    assert [k["name"] for k in client.get("/api/konten").json()["konten"]] == ["Extra-Konto", "Extra-Konto"]

    assert client.put("/api/konten/name", json={"schluessel": GIRO, "name": "  Urlaub   2027 "}).json() == {
        "schluessel": GIRO, "name": "Urlaub 2027", "farbe": ""}
    schluessel = db.depots()[0]["schluessel"]
    client.put("/api/konten/name", json={"schluessel": schluessel, "name": "Altersvorsorge"})
    daten = client.get("/api/konten").json()
    namen = {k["schluessel"]: (k["name"], k["eigener_name"], k["bezeichnung"]) for k in daten["konten"]}
    assert namen[GIRO] == ("Urlaub 2027", "Urlaub 2027", "Extra-Konto")
    assert namen[EXTRA] == ("Extra-Konto", "", "Extra-Konto")
    (d,) = daten["depots"]
    assert (d["name"], d["eigener_name"], d["bezeichnung"]) == ("Altersvorsorge", "Altersvorsorge", "Direkt-Depot")

    # Ein neuer Abruf überschreibt Bezeichnung und Depotname der Bank, der eigene Name bleibt
    db.bank_konto_speichern(konto(bezeichnung="Extra-Konto neu"))
    db.depot_speichern(depot.Depot(nummer="1234567890"), "2026-11-15T09:00:00")
    daten = client.get("/api/konten").json()
    assert {k["name"] for k in daten["konten"]} == {"Urlaub 2027", "Extra-Konto"}
    assert daten["depots"][0]["name"] == "Altersvorsorge"

    # Leer: wieder der Name der Bank
    client.put("/api/konten/name", json={"schluessel": GIRO, "name": ""})
    assert {k["name"] for k in client.get("/api/konten").json()["konten"]} == {"Extra-Konto neu", "Extra-Konto"}
    assert client.put("/api/konten/name", json={"schluessel": "gibt-es-nicht", "name": "x"}).status_code == 404
    assert client.put("/api/konten/name", json={"schluessel": GIRO, "name": "x" * 61}).status_code == 400


def test_vorgemerkt_unter_dem_kontostand(db, client):
    db.bank_konto_speichern(konto(saldo="459.82", saldo_stand="2026-11-14"))
    db.bank_konto_speichern(konto(EXTRA, saldo="1000.00", saldo_stand="2026-11-14"))
    db.konto_vorgemerkt_ersetzen(GIRO, [buchung("2026-11-24", "-12.99", 1), buchung("2026-11-23", "-2.99", 2)])
    daten = client.get("/api/konten").json()
    k = {k["schluessel"]: k for k in daten["konten"]}
    assert (k[GIRO]["vorgemerkt"], k[GIRO]["vorgemerkt_anzahl"]) == ("-15.98", 2)
    assert (k[EXTRA]["vorgemerkt"], k[EXTRA]["vorgemerkt_anzahl"]) == (None, 0)
    assert daten["summen"] == {"EUR": "1459.82"} and daten["summen_vorgemerkt"] == {"EUR": "-15.98"}


def test_api_verlauf(db, client):
    db.bank_konto_speichern(konto(saldo="500.00", saldo_stand="2026-11-14T08:00:00+00:00"))
    db.konto_buchungen_speichern(GIRO, [buchung("2026-11-12", "-50.00", 1), buchung("2026-11-13", "150.00", 2)])
    db.bank_konto_speichern(konto(EXTRA, saldo="1000.00", saldo_stand="2026-11-14"))
    db.bank_konto_speichern(konto("DE00USD", saldo="10.00", saldo_stand="2026-11-14", waehrung="USD"))

    giro = client.get("/api/konten/verlauf", params={"konto": GIRO}).json()
    assert giro == {"waehrung": "EUR", "punkte": [
        {"tag": "2026-11-11", "wert": "400.00"}, {"tag": "2026-11-12", "wert": "350.00"},
        {"tag": "2026-11-13", "wert": "500.00"}, {"tag": "2026-11-14", "wert": "500.00"}]}
    alle = client.get("/api/konten/verlauf").json()  # nur Euro-Konten
    assert [p["wert"] for p in alle["punkte"]] == ["1400.00", "1350.00", "1500.00", "1500.00"]
    assert client.get("/api/konten/verlauf", params={"konto": "x"}).status_code == 404


def test_metriken_vorgemerkt_und_verlauf(db):
    db.bank_konto_speichern(konto(saldo="459.82", saldo_stand="2026-11-14"))
    db.konto_vorgemerkt_ersetzen(GIRO, [buchung("2026-11-24", "-12.99", 1)])
    db.depot_speichern(depot.Depot(nummer="1234567890"), "2026-11-13T09:00:00")
    db.depot_speichern(depot.Depot(nummer="1234567890"), "2026-11-14T09:00:00")
    db.konto_name_setzen(GIRO, "Geheimer Name")
    text = monitoring.metriken(monitoring.Bestand(db, None, None))[0].decode()
    assert 'finanzomat_bankkonto_vorgemerkt{bank="ING",konto="ING"} 1.0' in text
    assert 'finanzomat_bankkonto_verlauf_tage{bank="ING",konto="ING"} 1.0' in text
    assert 'finanzomat_depot_verlauf_tage{bank="ING",depot="Direkt-Depot"} 2.0' in text
    offen = metriken.offen(db)
    for geheim in ("Geheimer Name", "459.82", "12.99"):
        assert geheim not in offen


def test_farbe(db, client):
    db.bank_konto_speichern(konto())
    db.bank_konto_speichern(konto(EXTRA))
    assert client.put("/api/konten/anzeige", json={"schluessel": GIRO, "farbe": "#14B8A6"}).json() == {
        "schluessel": GIRO, "name": "", "farbe": "#14b8a6"}
    # Name und Farbe ändern sich unabhängig voneinander
    client.put("/api/konten/anzeige", json={"schluessel": GIRO, "name": "Urlaub"})
    k = {k["schluessel"]: k for k in client.get("/api/konten").json()["konten"]}
    assert (k[GIRO]["name"], k[GIRO]["farbe"], k[EXTRA]["farbe"]) == ("Urlaub", "#14b8a6", "")
    client.put("/api/konten/anzeige", json={"schluessel": GIRO, "farbe": ""})
    assert db.konto_anzeige()[GIRO]["farbe"] == "" and db.konto_namen() == {GIRO: "Urlaub"}
    client.put("/api/konten/anzeige", json={"schluessel": GIRO, "name": ""})
    assert db.konto_anzeige() == {}  # nichts mehr eingestellt, Zeile weg
    for falsch in ("rot", "#12345", "#gggggg"):
        assert client.put("/api/konten/anzeige", json={"schluessel": GIRO, "farbe": falsch}).status_code == 400


def test_reihenfolge(db, client):
    drittes = "DE02120300000000202051"
    for s, bank in ((GIRO, "ING"), (EXTRA, "ING"), (drittes, "N26")):
        db.bank_konto_speichern(konto(s, bank=bank, bezeichnung=""))
    for nummer in ("1111111111", "2222222222"):
        db.depot_speichern(depot.Depot(nummer=nummer), "2026-11-14T09:00:00")
    reihe = lambda: [k["schluessel"] for k in client.get("/api/konten").json()["konten"]]  # noqa: E731
    assert reihe() == [EXTRA, GIRO, drittes]  # ohne Reihenfolge nach Bank und IBAN

    client.put("/api/konten/reihenfolge", json={"schluessel": [drittes, GIRO]})
    assert reihe() == [drittes, GIRO, EXTRA]  # nicht sortierte danach
    client.put("/api/konten/anzeige", json={"schluessel": GIRO, "name": "Zuerst?"})  # Name ändert den Platz nicht
    client.put("/api/konten/reihenfolge", json={"schluessel": [GIRO, drittes, EXTRA]})
    assert reihe() == [GIRO, drittes, EXTRA]

    erstes, zweites = (d["schluessel"] for d in client.get("/api/konten").json()["depots"])
    client.put("/api/konten/reihenfolge", json={"schluessel": [zweites, erstes]})
    assert [d["schluessel"] for d in client.get("/api/konten").json()["depots"]] == [zweites, erstes]
    assert "_platz" not in client.get("/api/konten").json()["konten"][0]

    assert client.put("/api/konten/reihenfolge", json={"schluessel": ["x"]}).status_code == 404
    assert client.put("/api/konten/reihenfolge", json={"schluessel": [GIRO, GIRO]}).status_code == 400


def test_alte_datenbank_bekommt_farbe_und_reihenfolge(tmp_path):
    import sqlite3

    from ustva.db import Datenbank

    pfad = tmp_path / "alt.sqlite"
    con = sqlite3.connect(pfad)
    con.execute("CREATE TABLE konto_namen (schluessel TEXT PRIMARY KEY, name TEXT NOT NULL)")
    con.execute("INSERT INTO konto_namen VALUES (?, 'Urlaub')", (GIRO,))
    con.commit()
    con.close()
    db = Datenbank(pfad)
    assert db.konto_anzeige()[GIRO] == {"schluessel": GIRO, "name": "Urlaub", "farbe": "", "reihenfolge": None}
    db.konto_farbe_setzen(GIRO, "#ef4444")
    assert db.konto_anzeige()[GIRO]["farbe"] == "#ef4444"
