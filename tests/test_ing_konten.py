"""Umsätze und Saldo der ING-Konten über FinTS (fiktive Daten, python-fints durch einen Nachbau ersetzt).

Die Buchungen gehen durch den echten MT940-Parser von python-fints, so wie die ING sie liefert.
"""

import argparse
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from fints.models import SEPAAccount
from fints.utils import mt940_to_array
from mt940.models import Amount, Balance, Date

from ustva import depot, ing_konten
from ustva import kontoabruf as ka
from ustva.config import Config
from ustva.models import BankKonto, Umsatz

from .test_depot import CFG, FakeFints, zugang

JETZT = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
ING_IBAN = "DE00500105171234567890"  # fiktiv
N26_IBAN = "DE00100110012345678901"

GEBUCHT = """
:20:STARTUMSE
:25:50010517/1234567890
:28C:00000/001
:60F:C261103EUR1000,00
:61:2611051105DR42,50NDDTNONREF
:86:105?00Lastschrift?20EREF+ABC123?21SVWZ+Strom November?30COBADEFFXXX?31DE00123456780000000001?32Muster Energie Gm
?33bH
:61:2611061106CR1500,00NTRFNONREF
:86:166?00Gutschrift?20SVWZ+Gehalt Oktober?32Beispiel AG
:62F:C261106EUR2457,50
-
"""
VORGEMERKT = """
:20:STARTUMSE
:25:50010517/1234567890
:28C:00000/001
:34F:EUR0,00
:13D:2611101200+0100
:61:2611101110DR9,99NDDTNONREF
:86:106?00Kartenzahlung?20SVWZ+Musterladen Berlin?32Musterladen
:90D:1EUR9,99
:90C:0EUR0,00
-
"""


def _mt940(text: str) -> str:
    return text.strip("\n").replace("\n?", "?").replace("\n", "\r\n") + "\r\n"


class FakeKonten(FakeFints):
    """Dazu Konten, Saldo und Buchungen."""

    def __init__(self, gebucht=GEBUCHT, vorgemerkt=VORGEMERKT, **kw):
        super().__init__(**kw)
        self.gebucht, self.vorgemerkt = gebucht, vorgemerkt
        self.zeitraum = []

    def get_sepa_accounts(self):
        return [SEPAAccount(iban=ING_IBAN, bic="INGDDEFFXXX", accountnumber="1234567890", subaccount=None,
                            blz="50010517"),
                SEPAAccount(iban="DE00500105179999999999", bic="INGDDEFFXXX", accountnumber="9999999999",
                            subaccount=None, blz="50010517")]  # nicht verbunden: wird nicht abgerufen

    def get_balance(self, konto):
        assert konto.iban == ING_IBAN
        return Balance("C", Amount("2447,51".replace(",", "."), "C", "EUR"), Date(2026, 11, 10))

    def get_transactions(self, konto, start_date=None, end_date=None, include_pending=False):
        self.zeitraum.append(start_date)
        return mt940_to_array(_mt940(self.gebucht) + (_mt940(self.vorgemerkt) if include_pending else ""))


def bankkonto(iban=ING_IBAN, bank="ING", **mehr) -> BankKonto:
    return BankKonto(**(dict(schluessel=iban, uid="u-" + iban[-4:], sitzung="s-1", bank=bank, land="DE",
                             konto=bank, iban=iban, aktiv=True) | mehr))


def test_abruf(db):
    db.bank_konto_speichern(bankkonto())
    db.bank_konto_speichern(bankkonto(N26_IBAN, "N26"))
    fake = FakeKonten()
    a = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT)
    assert (a.ergebnis, a.konten, a.neu, a.vorgemerkt) == ("ok", [ING_IBAN], 2, 1)
    assert fake.zeitraum == [date(2026, 10, 27)] * 2
    assert fake.abgefragt == []  # kein Depotbestand

    neueste, strom = db.konto_buchungen(ING_IBAN)
    assert strom["pruefsumme"].startswith("fints:")
    assert (strom["buchungsdatum"], strom["betrag"], strom["partner"], strom["iban"], strom["verwendungszweck"],
            strom["typ"]) == ("2026-11-05", "-42.50", "Muster Energie GmbH", "DE00123456780000000001",
                              "Strom November", "Lastschrift")
    assert (neueste["betrag"], neueste["partner"]) == ("1500.00", "Beispiel AG")
    (offen,) = db.konto_vorgemerkt(ING_IBAN)
    assert (offen["buchungsdatum"], offen["betrag"], offen["partner"]) == ("2026-11-10", "-9.99", "Musterladen")

    k = db.bank_konto(ING_IBAN)
    assert (k.saldo, k.saldo_stand, k.fints) == ("2447.51", JETZT.isoformat(), JETZT.isoformat())
    assert k.abgerufen == "" and k.abrufe == ""  # zählt nicht zu den Abrufen über Enable Banking
    assert db.bank_konto(N26_IBAN).fints == ""
    assert db.fints_zustand(depot.ZUGANG)["konten"] == JETZT.isoformat()

    # Ein späterer Abruf legt nichts doppelt an und ersetzt die vorgemerkten
    fake.vorgemerkt = VORGEMERKT.replace("9,99", "19,99")
    a = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT + timedelta(hours=1))
    assert (a.neu, len(db.konto_buchungen(ING_IBAN))) == (0, 2)
    assert [z["betrag"] for z in db.konto_vorgemerkt(ING_IBAN)] == ["-19.99"]


def test_enable_banking_speichert_fints_nicht_zurueck(db):
    """Enable Banking hält das Konto während seines Abrufs und speichert es danach ganz."""
    db.bank_konto_speichern(bankkonto())
    alt = db.bank_konto(ING_IBAN)
    ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT)
    db.bank_konto_speichern(alt)
    assert db.bank_konto(ING_IBAN).fints == JETZT.isoformat()


def test_schon_ueber_enable_banking(db):
    """Was Enable Banking geliefert hat (Buchungstag bis drei Tage anders), legt FinTS nicht noch einmal an."""
    db.bank_konto_speichern(bankkonto())
    eb = Umsatz(konto="ING", buchungsdatum=date(2026, 11, 6), betrag=D("-42.50"), partner="Muster Energie GmbH",
                pruefsumme="eb:1")
    db.konto_buchungen_speichern(ING_IBAN, [eb])
    a = ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT)
    assert a.neu == 1
    assert sorted(z["pruefsumme"][:3] for z in db.konto_buchungen(ING_IBAN)) == ["eb:", "fin"]


def test_enable_banking_uebernimmt(db):
    """Ein späterer Abruf über Enable Banking nimmt die Zeile von FinTS, statt eine zweite anzulegen."""
    db.bank_konto_speichern(bankkonto())
    ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT)
    eb = Umsatz(konto="ING", buchungsdatum=date(2026, 11, 4), betrag=D("-42.50"), pruefsumme="eb:strom")
    assert ka.eingelesene_uebernehmen(db, ING_IBAN, [eb]) == 1
    db.konto_buchungen_speichern(ING_IBAN, [eb])
    assert sorted(z["pruefsumme"][:3] for z in db.konto_buchungen(ING_IBAN)) == ["eb:", "fin"]
    # und FinTS legt sie danach nicht wieder an
    assert ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT + timedelta(hours=1)).neu == 0
    assert len(db.konto_buchungen(ING_IBAN)) == 2


def test_gleiche_umsaetze_einzeln(db):
    """Zwei gleiche Buchungen am selben Tag sind zwei Zeilen, auch wenn eine schon über Enable Banking kam."""
    zweimal = GEBUCHT.replace(":62F:", ":61:2611051105DR42,50NDDTNONREF\n:86:105?00Lastschrift?20SVWZ+Strom "
                                       "November?31DE00123456780000000001?32Muster Energie GmbH\n:62F:")
    db.bank_konto_speichern(bankkonto())
    db.konto_buchungen_speichern(ING_IBAN, [Umsatz(konto="ING", buchungsdatum=date(2026, 11, 5), betrag=D("-42.50"),
                                                   pruefsumme="eb:1")])
    assert ing_konten.abrufen(db, CFG, zugang(FakeKonten(gebucht=zweimal)), jetzt=JETZT).neu == 2
    assert [z["betrag"] for z in db.konto_buchungen(ING_IBAN)].count("-42.50") == 2


def test_nur_wenn_aelter_und_freigabe(db):
    db.bank_konto_speichern(bankkonto())
    fake = FakeKonten()
    assert ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT).ergebnis == "ok"
    bald = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT + timedelta(minutes=1),
                              wenn_aelter=ing_konten.BEIM_OEFFNEN)
    assert bald.uebersprungen == "gerade erst abgerufen"
    spaeter = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT + timedelta(minutes=3),
                                 wenn_aelter=ing_konten.BEIM_OEFFNEN)
    assert spaeter.ergebnis == "ok"

    # Freigabe kommt nicht: danach fragt nur noch der Knopf
    a = ing_konten.abrufen(db, CFG, zugang(FakeKonten(freigaben=999)), jetzt=JETZT + timedelta(minutes=10))
    assert (a.ergebnis, db.fints_zustand(depot.ZUGANG)["freigabe"]) == ("freigabe", True)
    auto = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT + timedelta(hours=1), hintergrund=True)
    assert auto.ergebnis == "uebersprungen" and "Freigabe" in auto.uebersprungen
    a = ing_konten.abrufen(db, CFG, zugang(FakeKonten(freigaben=1)), jetzt=JETZT + timedelta(hours=1))
    assert (a.ergebnis, db.fints_zustand(depot.ZUGANG)["freigabe"]) == ("ok", False)


def test_fehler_und_ohne_ing(db):
    a = ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT)
    assert a.uebersprungen == "kein Konto der ING verbunden"
    db.bank_konto_speichern(bankkonto())
    a = ing_konten.abrufen(db, CFG, zugang(FakeKonten(fehler=RuntimeError("Bank antwortet nicht"))), jetzt=JETZT)
    assert (a.ergebnis, a.fehler) == ("fehler", "RuntimeError: Bank antwortet nicht")
    assert db.fints_zustand(depot.ZUGANG)["konten"] == JETZT.isoformat()  # auch ein Fehler zählt als Versuch
    assert db.bank_konto(ING_IBAN).fints == ""
    a = ing_konten.abrufen(db, Config(), jetzt=JETZT)
    assert a.ergebnis == "fehler" and "nicht eingerichtet" in a.fehler


def test_name_und_iban():
    assert ing_konten._name_und_iban("DE00123456780000000001Muster Energie GmbH") == (
        "Muster Energie GmbH", "DE00123456780000000001")
    assert ing_konten._name_und_iban("NL00TEST0123456789Voorbeeld BV") == ("Voorbeeld BV", "NL00TEST0123456789")
    assert ing_konten._name_und_iban("DE Bahn") == ("DE Bahn", "")
    assert ing_konten._name_und_iban("Deutsche Post") == ("Deutsche Post", "")


def test_hintergrund_stuendlich(db, monkeypatch):
    aufrufe = []

    def abrufen(db_, cfg, zugang=None, jetzt=None, hintergrund=False, wenn_aelter=None, **_):
        aufrufe.append((jetzt, hintergrund, wenn_aelter))
        return ing_konten.KontenAbruf()

    monkeypatch.setattr(ing_konten, "abrufen", abrufen)
    h = ka.Hintergrundabruf(db, CFG, ["16:00"], tz=ZoneInfo("Europe/Berlin"))
    h.einmal(jetzt=datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    assert aufrufe == []  # noch kein Konto der ING
    db.bank_konto_speichern(bankkonto())
    h.einmal(jetzt=datetime(2026, 11, 10, 5, 59, tzinfo=UTC))  # 6:59 deutsche Zeit
    assert aufrufe == []
    for minute in (0, 30, 60, 61):
        h.einmal(jetzt=datetime(2026, 11, 10, 6, 0, tzinfo=UTC) + timedelta(minutes=minute))
    assert [a[0].hour for a in aufrufe] == [6, 7]
    assert all(a[1] and a[2] == timedelta(minutes=50) for a in aufrufe)
    h.einmal(jetzt=datetime(2026, 11, 10, 21, 30, tzinfo=UTC))  # 22:30 deutsche Zeit
    assert len(aufrufe) == 2


def test_api(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ustva import konten_web

    db.bank_konto_speichern(bankkonto())
    fake = FakeKonten()
    uhr = {"jetzt": JETZT}
    app = FastAPI()
    app.include_router(konten_web.router(db, lambda: CFG, jetzt=lambda: uhr["jetzt"],
                                         depot_fabrik=lambda cfg, z: zugang(fake, z)))
    client = TestClient(app)
    uebersicht = client.get("/api/konten").json()
    assert uebersicht["ing"] == {"eingerichtet": True, "versucht": "", "beim_oeffnen": 120}
    assert uebersicht["konten"][0]["ing"] is True

    antwort = client.post("/api/konten/ing/abrufen", params={"automatisch": True}).json()
    assert antwort == {"fehler": "", "freigabe": False, "uebersprungen": "", "neu": 2, "vorgemerkt": 1,
                       "konten": [ING_IBAN]}
    assert client.post("/api/konten/ing/abrufen", params={"automatisch": True}).json()["uebersprungen"]
    assert client.post("/api/konten/ing/abrufen").json()["uebersprungen"] == ""  # der Knopf ruft immer ab
    (k,) = client.get("/api/konten").json()["konten"]
    assert (k["saldo"], k["fints"]) == ("2447.51", JETZT.isoformat())

    nicht = FastAPI()
    nicht.include_router(konten_web.router(db, lambda: Config()))
    assert TestClient(nicht).post("/api/konten/ing/abrufen").status_code == 400
    assert TestClient(nicht).get("/api/konten").json()["ing"]["eingerichtet"] is False


def test_metriken(db):
    from ustva import monitoring

    db.bank_konto_speichern(bankkonto())
    a = ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT)
    monitoring.ing_abruf_zaehlen(a, "knopf")
    text = monitoring.metriken(monitoring.Bestand(db, None, None))[0].decode()
    assert 'finanzomat_bankkonto_fints_abruf_timestamp_seconds{bank="ING",konto="ING"}' in text
    assert 'finanzomat_ing_fints_abruf_total{ausloeser="knopf",ergebnis="ok"}' in text
    assert "2447" not in text and ING_IBAN not in text


def test_befehl(db, capsys):
    from ustva.__main__ import ing_befehl

    db.bank_konto_speichern(bankkonto(bezeichnung="Girokonto"))
    args = argparse.Namespace(schritt="ing", warten=120)
    assert ing_befehl(args, CFG, db, zugang(FakeKonten(freigaben=1))) == 0
    out = capsys.readouterr().out
    assert "ING-App freigeben" in out and "Girokonto …7890: Saldo 2447.51 EUR" in out
    assert "2 neu in der Kontenübersicht, 1 vorgemerkt" in out
    assert ing_befehl(args, Config(), db) == 1


def test_anmeldung_abgelehnt_haelt_automatik_an(db):
    from .test_depot import AbgelehnteAnmeldung

    db.bank_konto_speichern(bankkonto())
    fake = AbgelehnteAnmeldung()
    a = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT)
    assert a.ergebnis == "fehler" and "9942 PIN falsch" in a.fehler
    auto = ing_konten.abrufen(db, CFG, zugang(fake), jetzt=JETZT + timedelta(hours=1), hintergrund=True,
                              wenn_aelter=ing_konten.BEIM_OEFFNEN)
    assert "abgelehnt" in auto.uebersprungen and fake.anmeldungen == 1
    # auch der Depotabruf im Hintergrund nicht
    assert "abgelehnt" in depot.abrufen(db, CFG, zugang(fake), jetzt=JETZT, hintergrund=True).uebersprungen
    assert ing_konten.abrufen(db, CFG, zugang(FakeKonten()), jetzt=JETZT + timedelta(hours=2)).ergebnis == "ok"
    assert db.fints_zustand(depot.ZUGANG)["abgelehnt"] is False
