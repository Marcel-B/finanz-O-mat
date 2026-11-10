import argparse
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ustva import bank
from ustva import kontoabruf as ka
from ustva.__main__ import bank_befehl
from ustva.config import Config
from ustva.models import BankKonto

# Fiktive Konten und Partner
IBAN = "DE89370400440532013000"
JETZT = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def schluessel():
    privat = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = privat.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    return pem, privat.public_key()


def buchung(betrag, richtung, tag, **mehr):
    return {"transaction_amount": {"amount": betrag, "currency": "EUR"}, "credit_debit_indicator": richtung,
            "status": "BOOK", "booking_date": tag, **mehr}


BUCHUNGEN = [
    buchung("59.50", "DBIT", "2026-09-12", entry_reference="E1", creditor={"name": "Hosting GmbH"},
            creditor_account={"iban": "DE02120300000000202051"}, remittance_information=["SVWZ+R-2026-0912"]),
    buchung("4522.00", "CRDT", "2026-09-28", entry_reference="E2", debtor={"name": "Beispiel AG"},
            debtor_account={"iban": "DE02500105170137075030"}, remittance_information=["Rechnung 2026-030"]),
    buchung("17.32", "DBIT", "2026-09-03", transaction_id="T3", creditor={"name": "OpenAI"},
            exchange_rate={"instructed_amount": {"amount": "20.00", "currency": "USD"}}),
    buchung("812.40", "DBIT", "2026-09-29", entry_reference="E4", creditor={"name": "Finanzamt Musterstadt"}),
    # zwei gleiche Kartenzahlungen ohne Kennung
    buchung("3.80", "DBIT", "2026-09-30", creditor={"name": "Café Kaffeebohne"}),
    buchung("3.80", "DBIT", "2026-09-30", creditor={"name": "Café Kaffeebohne"}),
    {**buchung("9.99", "DBIT", "2026-10-01", creditor={"name": "Vorgemerkt"}), "status": "PDNG"},
]


PRIVAT = [
    buchung("42.00", "DBIT", "2026-10-02", entry_reference="P1", creditor={"name": "Supermarkt"}),
    buchung("2500.00", "CRDT", "2026-10-30", entry_reference="P2", debtor={"name": "Umbuchung"}),
]


class Bank:
    """Nachbau der API von Enable Banking."""

    def __init__(self, oeffentlich, buchungen=BUCHUNGEN):
        self.oeffentlich = oeffentlich
        self.buchungen = buchungen
        self.state = None
        self.anfragen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        assert jwt.get_unverified_header(token)["kid"] == "app-123"
        jwt.decode(token, self.oeffentlich, algorithms=["RS256"], audience="api.enablebanking.com")
        self.anfragen.append(request)
        pfad = request.url.path
        if pfad == "/application":
            return httpx.Response(200, json={"redirect_urls": ["https://finanz.example/bank-verbunden"]})
        if pfad == "/aspsps":
            return httpx.Response(200, json={"aspsps": [
                {"name": "N26", "country": "DE", "maximum_consent_validity": 90 * 86400, "psu_types": ["personal"]},
                {"name": "ING", "country": "DE", "maximum_consent_validity": 180 * 86400},
                {"name": "ING Wholesale", "country": "DE"},
            ]})
        if pfad == "/auth":
            body = json.loads(request.content)
            self.state = body["state"]
            self.auth = body
            return httpx.Response(200, json={"url": "https://bank.example/login?x=1"})
        if pfad == "/sessions":
            assert json.loads(request.content) == {"code": "geheim"}
            return httpx.Response(200, json={
                "session_id": "s-1", "access": {"valid_until": "2027-02-08T08:00:00+00:00"},
                "accounts": [{"uid": "u-1", "account_id": {"iban": IBAN}, "name": "Hauptkonto", "currency": "EUR"},
                             {"uid": "u-2", "account_id": {"iban": "DE75512108001245126199"}, "name": "Spaces"}],
            })
        if pfad == "/accounts/u-1/balances":
            return httpx.Response(200, json={"balances": [
                {"balance_amount": {"amount": "1200.00", "currency": "EUR"}, "balance_type": "ITAV"},
                {"balance_amount": {"amount": "1069.77", "currency": "EUR"}, "balance_type": "CLBD",
                 "reference_date": "2026-11-09"}]})
        if pfad == "/accounts/u-2/balances":
            return httpx.Response(200, json={"balances": [
                {"balance_amount": {"amount": "194.98", "currency": "EUR"}, "balance_type": "ITBD"}]})
        if pfad == "/accounts/u-2/transactions":
            return httpx.Response(200, json={"transactions": PRIVAT})
        if pfad == "/accounts/u-1/transactions":
            # zwei Seiten
            if request.url.params.get("continuation_key") == "weiter":
                return httpx.Response(200, json={"transactions": self.buchungen[3:]})
            return httpx.Response(200, json={"transactions": self.buchungen[:3], "continuation_key": "weiter"})
        return httpx.Response(404, json={"message": "unbekannt"})


@pytest.fixture
def eb(schluessel):
    pem, oeffentlich = schluessel
    api = Bank(oeffentlich)
    client = ka.EnableBanking("app-123", pem, httpx.Client(transport=httpx.MockTransport(api)))
    client.api_nachbau = api
    return client


def konto(**mehr):
    werte = dict(schluessel=IBAN, uid="u-1", sitzung="s-1", bank="N26", land="DE", konto="N26", iban=IBAN,
                 aktiv=True, gueltig_bis="2027-02-08T08:00:00+00:00")
    return BankKonto(**(werte | mehr))


def test_umsatz_aus_buchung():
    k = konto()
    u = ka.umsatz_aus(BUCHUNGEN[0], k)
    assert (u.betrag, u.partner, u.iban, u.verwendungszweck, u.buchungsdatum) == (
        D("-59.50"), "Hosting GmbH", "DE02120300000000202051", "R-2026-0912", date(2026, 9, 12))
    eingang = ka.umsatz_aus(BUCHUNGEN[1], k)
    assert (eingang.betrag, eingang.partner) == (D("4522.00"), "Beispiel AG")
    fremd = ka.umsatz_aus(BUCHUNGEN[2], k)
    assert (fremd.betrag, fremd.original_betrag, fremd.original_waehrung) == (D("-17.32"), D("-20.00"), "USD")
    assert ka.umsatz_aus(BUCHUNGEN[-1], k) is None  # vorgemerkt


def test_pruefsummen_stabil_und_getrennt():
    k = konto()
    paare = [(tx, ka.umsatz_aus(tx, k)) for tx in BUCHUNGEN[:-1]]
    ka.pruefsummen(paare, k)
    summen = [u.pruefsumme for _, u in paare]
    assert all(s.startswith("eb:") for s in summen) and len(set(summen)) == len(summen)
    # neue Freigabe = neue uid, gleiche Prüfsummen
    k2 = konto()
    k2.uid = "u-neu"
    paare2 = [(tx, ka.umsatz_aus(tx, k2)) for tx in BUCHUNGEN[:-1]]
    ka.pruefsummen(paare2, k2)
    assert [u.pruefsumme for _, u in paare2] == summen


def test_jwt_wird_wiederverwendet(schluessel):
    pem, _ = schluessel
    zeit = [1_000_000.0]
    eb = ka.EnableBanking("app-123", pem, uhr=lambda: zeit[0])
    erstes = eb._jwt()
    zeit[0] += 600
    assert eb._jwt() == erstes
    zeit[0] += 3000  # kurz vor Ablauf ein neues
    assert eb._jwt() != erstes


def test_falscher_schluessel():
    eb = ka.EnableBanking("app-123", b"kein Schluessel")
    with pytest.raises(ka.KontoabrufFehler, match="unbrauchbar"):
        eb._jwt()


def test_nicht_eingerichtet():
    with pytest.raises(ka.KontoabrufFehler, match="ENABLE_BANKING_APP_ID"):
        ka.EnableBanking.aus_config(Config())


def test_code_aus():
    assert ka.code_aus("https://finanz.example/bank-verbunden?code=abc&state=s1", "s1") == "abc"
    assert ka.code_aus("  abc ") == "abc"
    with pytest.raises(ka.KontoabrufFehler, match="state"):
        ka.code_aus("https://finanz.example/?code=abc&state=anders", "s1")
    with pytest.raises(ka.KontoabrufFehler, match="nicht erteilt"):
        ka.code_aus("https://finanz.example/?error=access_denied&state=s1", "s1")
    with pytest.raises(ka.KontoabrufFehler, match="kein code"):
        ka.code_aus("https://finanz.example/?state=s1", "s1")


def test_bank_finden():
    banken = [{"name": "ING"}, {"name": "ING Wholesale"}, {"name": "N26"}]
    assert ka.bank_finden(banken, "ing")["name"] == "ING"
    assert ka.bank_finden(banken, "whole")["name"] == "ING Wholesale"
    with pytest.raises(ka.KontoabrufFehler, match="Keine Bank"):
        ka.bank_finden(banken, "bunq")


def test_freigabe_bis():
    assert ka.freigabe_bis({"maximum_consent_validity": 90 * 86400}, JETZT).date() == date(2027, 2, 8)
    assert ka.freigabe_bis({}, JETZT).date() == (JETZT + timedelta(days=180)).date()


def args(**werte):
    return argparse.Namespace(**{"land": "DE", "geschaeftlich": False, "rueckleitung": None, **werte})


def test_verbinden_und_konto_einschalten(db, eb, capsys, monkeypatch):
    api = eb.api_nachbau
    eingabe = lambda frage: f"https://finanz.example/bank-verbunden?state={api.state}&code=geheim"  # noqa: E731
    assert bank_befehl(args(schritt="verbinden", bank="n26"), Config(), db, eingabe, eb.client) == 1  # ohne Config
    cfg = Config()
    # Der Befehl baut den Client aus der Config; hier den Nachbau unterschieben
    monkeypatch.setattr(ka.EnableBanking, "aus_config", classmethod(lambda cls, cfg, client=None: eb))
    assert bank_befehl(args(schritt="verbinden", bank="n26"), cfg, db, eingabe) == 0
    assert api.auth["aspsp"] == {"name": "N26", "country": "DE"}
    assert api.auth["redirect_url"] == "https://finanz.example/bank-verbunden"
    assert api.auth["psu_type"] == "personal"
    ausgabe = capsys.readouterr().out
    assert "https://bank.example/login?x=1" in ausgabe and "Neue Konten sind aus" in ausgabe
    konten = db.bank_konten()
    assert [(k.iban, k.konto, k.aktiv) for k in konten] == [
        ("DE75512108001245126199", "N26", False), (IBAN, "N26", False)]

    assert bank_befehl(args(schritt="konto", konto="de89 3704 0044 0532 0130 00", an=True, name=None), cfg, db) == 0
    assert db.bank_konto(IBAN).aktiv

    # Erneut verbinden: Schalter und Name bleiben, uid und Gültigkeit kommen neu
    k = db.bank_konto(IBAN)
    k.konto, k.uid, k.gueltig_bis = "Geschäft", "alt", ""
    db.bank_konto_speichern(k)
    neu, erneuert = ka.sitzung_uebernehmen(db, {"session_id": "s-2", "access": {"valid_until": "2027-05-01"},
                                                "accounts": [{"uid": "u-9", "account_id": {"iban": IBAN}}]},
                                           "N26", "DE")
    assert not neu
    k = db.bank_konto(IBAN)
    assert (k.konto, k.aktiv, k.uid, k.sitzung, k.gueltig_bis, k.bezeichnung) == (
        "Geschäft", True, "u-9", "s-2", "2027-05-01", "Hauptkonto")


PRIVAT_IBAN = "DE75512108001245126199"


def privat():
    return BankKonto(schluessel=PRIVAT_IBAN, uid="u-2", sitzung="s-1", bank="ING", land="DE", konto="ING",
                     iban=PRIVAT_IBAN, gueltig_bis="2027-05-01T00:00:00+00:00")


def test_abrufen(db, eb):
    db.bank_konto_speichern(konto())
    db.bank_konto_speichern(privat())  # ausgeschaltet: nur Kontenübersicht
    e = ka.abrufen(db, Config(), eb, heute=date(2026, 11, 10), jetzt=JETZT)
    a = next(a for a in e.konten if a.konto.schluessel == IBAN)
    assert (a.neu, a.vorhanden, a.vorgemerkt, a.uebersicht, a.fehler) == (6, 0, 1, 6, "")
    params = next(r for r in eb.api_nachbau.anfragen if r.url.path == "/accounts/u-1/transactions").url.params
    assert params["date_from"] == "2026-08-12"  # erster Abruf: 90 Tage
    umsaetze = db.alle_umsaetze()
    assert len(umsaetze) == 6  # nur das eingeschaltete Konto in der Buchhaltung
    finanzamt = next(u for u in umsaetze if u.partner.startswith("Finanzamt"))
    assert finanzamt.ignoriert  # BANK_IGNORIEREN gilt auch hier
    k = db.bank_konto(IBAN)
    assert (k.bis, k.abgerufen, k.saldo, k.saldo_stand) == (
        "2026-09-30", "2026-11-10T08:00:00+00:00", "1069.77", "2026-11-09")
    p = next(a for a in e.konten if a.konto.schluessel == PRIVAT_IBAN)
    assert (p.neu, p.uebersicht) == (0, 2)
    kp = db.bank_konto(PRIVAT_IBAN)
    assert (kp.saldo, kp.saldo_stand) == ("194.98", "2026-11-10T08:00:00+00:00")
    assert [b["betrag"] for b in db.konto_buchungen(PRIVAT_IBAN)] == ["2500.00", "-42.00"]  # neueste zuerst
    assert len(db.konto_buchungen()) == 8

    # Zweiter Abruf: nichts doppelt, Beginn eine Woche vor dem letzten Buchungstag
    eb.api_nachbau.anfragen.clear()
    a = ka.abrufen(db, Config(), eb, heute=date(2026, 11, 11), jetzt=JETZT, nur=IBAN).konten[0]
    assert (a.neu, a.vorhanden, a.uebersicht) == (0, 6, 0)
    assert eb.api_nachbau.anfragen[0].url.params["date_from"] == "2026-09-23"
    assert len(db.alle_umsaetze()) == 6
    assert len(db.konto_buchungen()) == 8


def test_abruflimit_und_faellig(db, eb):
    db.bank_konto_speichern(konto())
    for stunde in range(4):
        a = ka.abrufen(db, Config(), eb, jetzt=JETZT + timedelta(hours=stunde)).konten[0]
        assert not a.uebersprungen
    eb.api_nachbau.anfragen.clear()
    a = ka.abrufen(db, Config(), eb, jetzt=JETZT + timedelta(hours=5)).konten[0]
    assert "4 Abrufe in 24 Stunden" in a.uebersprungen and not eb.api_nachbau.anfragen
    k = db.bank_konto(IBAN)
    assert ka.abrufe_frei(k, JETZT + timedelta(hours=5)) == 0
    assert ka.naechster_freier_abruf(k, JETZT + timedelta(hours=5)) == JETZT + timedelta(hours=24)
    # 24 Stunden nach dem ersten ist wieder einer frei
    assert ka.abrufe_frei(k, JETZT + timedelta(hours=24, minutes=1)) == 1
    # regelmäßiger Abruf: erst fällig, wenn der letzte (nach 3 Stunden) lange genug her ist
    assert not ka.abrufen(db, Config(), eb, jetzt=JETZT + timedelta(hours=8), faellig_nach=timedelta(hours=6)).konten
    a = ka.abrufen(db, Config(), eb, jetzt=JETZT + timedelta(hours=10), faellig_nach=timedelta(hours=6)).konten[0]
    assert a.uebersprungen  # fällig, aber das Limit gilt auch hier
    a = ka.abrufen(db, Config(), eb, jetzt=JETZT + timedelta(hours=25), faellig_nach=timedelta(hours=6)).konten[0]
    assert not a.uebersprungen and not a.fehler


def test_einschalten_uebernimmt_aus_uebersicht(db, eb):
    db.bank_konto_speichern(privat())
    ka.abrufen(db, Config(), eb, jetzt=JETZT)
    assert not db.alle_umsaetze()
    assert bank_befehl(args(schritt="konto", konto=PRIVAT_IBAN, an=True, name=None), Config(), db) == 0
    umsaetze = db.alle_umsaetze()
    assert [(u.konto, u.betrag, u.partner) for u in umsaetze] == [
        ("ING", D("-42.00"), "Supermarkt"), ("ING", D("2500.00"), "Umbuchung")]
    # ein späterer Abruf legt sie nicht noch einmal an
    a = ka.abrufen(db, Config(), eb, jetzt=JETZT + timedelta(hours=7)).konten[0]
    assert (a.neu, a.vorhanden) == (0, 2)


def test_hintergrund_zu_festen_zeiten(db, eb):
    from zoneinfo import ZoneInfo

    h = ka.Hintergrundabruf(db, Config(), ["09:00", "12:30", "16:00", "19:30"], tz=ZoneInfo("Europe/Berlin"))
    assert h.einmal(eb, jetzt=JETZT) is None  # noch keine Konten
    db.bank_konto_speichern(konto())
    e = h.einmal(eb, jetzt=JETZT + timedelta(minutes=1))  # 9:01 Uhr deutscher Zeit
    assert e.konten[0].uebersicht == 6
    assert h.einmal(eb, jetzt=JETZT + timedelta(minutes=2)) is None  # 9:00 schon erledigt
    assert h.einmal(eb, jetzt=JETZT + timedelta(hours=2)) is None  # 11:00, keine Abrufzeit
    assert h.einmal(eb, jetzt=JETZT.replace(hour=21)) is None  # 22:00, nachts nicht

    # Neustart des Dienstes um 9:30: das Konto ist seit 9:00 schon abgerufen
    h2 = ka.Hintergrundabruf(db, Config(), ["09:00", "12:30"], tz=ZoneInfo("Europe/Berlin"))
    assert not h2.einmal(eb, jetzt=JETZT + timedelta(minutes=30)).konten
    # 12:30 verpasst, um 13:15 nachgeholt; 16:00 um 17:15 nicht mehr
    assert h2.einmal(eb, jetzt=JETZT.replace(hour=12, minute=15)).konten[0].uebersprungen == ""
    assert len(db.bank_konto(IBAN).abrufe.split()) == 2
    h3 = ka.Hintergrundabruf(db, Config(), ["16:00"], tz=ZoneInfo("Europe/Berlin"))
    assert h3.einmal(eb, jetzt=JETZT.replace(hour=16, minute=15)) is None  # 17:15


def test_faellige_zeit():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Berlin")
    zeiten = ["09:00", "12:30", "16:00", "19:30"]
    assert ka.faellige_zeit(zeiten, datetime(2026, 11, 10, 8, 59, tzinfo=tz)) is None
    assert ka.faellige_zeit(zeiten, datetime(2026, 11, 10, 9, 0, tzinfo=tz)).hour == 9
    assert ka.faellige_zeit(zeiten, datetime(2026, 11, 10, 12, 45, tzinfo=tz)).minute == 30
    assert ka.faellige_zeit(zeiten, datetime(2026, 11, 10, 20, 31, tzinfo=tz)) is None
    assert ka.faellige_zeit([], datetime(2026, 11, 10, 9, 0, tzinfo=tz)) is None


def test_abrufzeiten_aus_umgebung(monkeypatch):
    monkeypatch.delenv("KONTOABRUF_ZEITEN", raising=False)
    assert Config.aus_umgebung().kontoabruf_zeiten == ["09:00", "12:30", "16:00", "19:30"]
    monkeypatch.setenv("KONTOABRUF_ZEITEN", "19:30, 9:00 Uhr;12.30, 25:00, quatsch")
    assert Config.aus_umgebung().kontoabruf_zeiten == ["09:00", "12:30", "19:30"]
    monkeypatch.setenv("KONTOABRUF_ZEITEN", "aus")
    assert Config.aus_umgebung().kontoabruf_zeiten == []


def test_alte_datenbank_bekommt_neue_spalten(tmp_path):
    import sqlite3

    from ustva.db import Datenbank

    pfad = tmp_path / "alt.sqlite"
    con = sqlite3.connect(pfad)
    con.execute("CREATE TABLE bank_konten (schluessel TEXT PRIMARY KEY, uid TEXT NOT NULL, sitzung TEXT NOT NULL, "
                "bank TEXT NOT NULL, land TEXT NOT NULL, iban TEXT NOT NULL DEFAULT '', bezeichnung TEXT NOT NULL "
                "DEFAULT '', waehrung TEXT NOT NULL DEFAULT 'EUR', konto TEXT NOT NULL, aktiv INTEGER NOT NULL "
                "DEFAULT 0, gueltig_bis TEXT NOT NULL DEFAULT '', abgerufen TEXT NOT NULL DEFAULT '', bis TEXT NOT "
                "NULL DEFAULT '')")
    con.execute("INSERT INTO bank_konten (schluessel, uid, sitzung, bank, land, konto) "
                "VALUES ('X', 'u', 's', 'ING', 'DE', 'ING')")
    con.commit()
    con.close()
    k = Datenbank(pfad).bank_konto("X")
    assert (k.konto, k.saldo, k.abrufe, k.fehler) == ("ING", "", "", "")


def test_abrufen_nach_csv_und_csv_nach_abrufen(db, eb):
    from tests.test_bank import N26_NEU

    # Erst CSV, dann Abruf: die CSV-Zeilen bleiben (mit Markierung), nichts doppelt
    assert bank.importieren(db, Config(), N26_NEU).neu == 6
    kaffee = next(u for u in db.alle_umsaetze() if u.partner.startswith("Café"))
    kaffee.ignoriert, kaffee.ignoriert_grund = True, "privat"
    db.umsatz_aktualisieren(kaffee)
    db.bank_konto_speichern(konto())
    a = ka.abrufen(db, Config(), eb, heute=date(2026, 11, 10), jetzt=JETZT).konten[0]
    assert (a.neu, a.aus_csv) == (0, 6)
    umsaetze = db.alle_umsaetze()
    assert len(umsaetze) == 6 and all(u.pruefsumme.startswith("eb:") for u in umsaetze)
    assert db.umsatz(kaffee.id).ignoriert_grund == "privat"
    # CSV noch einmal: erkennt die abgerufenen Umsätze
    e = bank.importieren(db, Config(), N26_NEU)
    assert (e.neu, e.vorhanden) == (0, 6)
    assert len(db.alle_umsaetze()) == 6


def test_csv_nach_abrufen(db, eb):
    from tests.test_bank import N26_NEU

    db.bank_konto_speichern(konto())
    ka.abrufen(db, Config(), eb, heute=date(2026, 11, 10), jetzt=JETZT)
    e = bank.importieren(db, Config(), N26_NEU)
    assert (e.neu, e.vorhanden) == (0, 6)
    # Eine dritte gleiche Kartenzahlung im CSV ist neu
    zeile = '"2026-09-30","2026-09-30","Café Kaffeebohne","","Presentment","","Hauptkonto","-3.80","","",""\n'
    dritte = N26_NEU + zeile
    assert bank.importieren(db, Config(), dritte).neu == 1


def test_abgelaufene_freigabe_und_fehler(db, eb):
    db.bank_konto_speichern(konto(gueltig_bis="2026-11-01T00:00:00+00:00"))
    e = ka.abrufen(db, Config(), eb, heute=date(2026, 11, 10), jetzt=JETZT)
    assert "abgelaufen" in e.fehler[0]
    assert not eb.api_nachbau.anfragen

    k = konto(uid="u-unbekannt")
    db.bank_konto_speichern(k)
    e = ka.abrufen(db, Config(), eb, heute=date(2026, 11, 10), jetzt=JETZT)
    assert "404" in e.fehler[0] and "unbekannt" in e.fehler[0]


def test_probelauf_speichert_nichts(db, eb):
    db.bank_konto_speichern(konto(aktiv=False))  # aus, aber ausdrücklich gewählt
    e = ka.abrufen(db, Config(), eb, heute=date(2026, 11, 10), jetzt=JETZT, trocken=True, nur=IBAN.lower())
    a = e.konten[0]
    assert (a.neu, len(a.umsaetze), a.vorgemerkt) == (6, 6, 1)
    assert not db.alle_umsaetze()
    k = db.bank_konto(IBAN)
    assert (k.abgerufen, k.bis) == ("", "")


def test_probelauf_befehl(db, eb, capsys, monkeypatch):
    db.bank_konto_speichern(konto(aktiv=False))
    monkeypatch.setattr(ka.EnableBanking, "aus_config", classmethod(lambda cls, cfg, client=None: eb))
    assert bank_befehl(args(schritt="abrufen", von=None, konto=IBAN, trocken=True), Config(), db) == 0
    ausgabe = capsys.readouterr().out
    assert "PROBELAUF" in ausgabe and "Hosting GmbH" in ausgabe and "-17,32 € (-20,00 USD)" in ausgabe
    assert not db.alle_umsaetze()


def test_api_konten(db, eb):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ustva import konten_web

    app = FastAPI()
    cfg = Config(enable_banking_app_id="app-123")
    app.include_router(konten_web.router(db, lambda: cfg, lambda c: eb, jetzt=lambda: JETZT))
    client = TestClient(app)
    leer = client.get("/api/konten").json()
    assert (leer["konten"], leer["eingerichtet"], leer["abrufe_je_tag"]) == ([], False, 4)

    db.bank_konto_speichern(konto())
    db.bank_konto_speichern(privat())
    antwort = client.post("/api/konten/abrufen").json()
    assert sorted(k["neu"] for k in antwort["konten"]) == [2, 6]
    daten = client.get("/api/konten").json()
    assert daten["summen"] == {"EUR": "1264.75"}
    ing, n26 = daten["konten"]
    assert (ing["bank"], ing["saldo"], ing["buchhaltung"], ing["abrufe_frei"], ing["freigabe_tage"]) == (
        "ING", "194.98", False, 3, 171)
    assert (n26["bank"], n26["buchhaltung"]) == ("N26", True)
    alle = client.get("/api/konten/buchungen").json()
    nur_ing = client.get("/api/konten/buchungen", params={"konto": PRIVAT_IBAN}).json()
    assert len(alle) == 8 and [b["partner"] for b in nur_ing] == ["Umbuchung", "Supermarkt"]


def test_api_abrufen_ohne_einrichtung(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ustva import konten_web

    app = FastAPI()
    app.include_router(konten_web.router(db, Config))
    antwort = TestClient(app).post("/api/konten/abrufen")
    assert antwort.status_code == 400 and "ENABLE_BANKING_APP_ID" in antwort.json()["detail"]
