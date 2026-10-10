from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from ustva import bank
from ustva.berechnung import berechnen
from ustva.config import Config
from ustva.models import Beleg
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app

# Aktuelles N26-Exportformat (fiktive Daten)
N26_NEU = """"Booking Date","Value Date","Partner Name","Partner Iban","Type","Payment Reference","Account Name","Amount (EUR)","Original Amount","Original Currency","Exchange Rate"
"2026-09-03","2026-09-03","OpenAI","","Presentment","","Hauptkonto","-17.32","-20.00","USD","1.1547"
"2026-09-12","2026-09-12","Hosting GmbH","DE02120300000000202051","Direct Debit","R-2026-0912","Hauptkonto","-59.50","","",""
"2026-09-28","2026-09-28","Beispiel AG","DE02500105170137075030","Credit Transfer","Rechnung 2026-030","Hauptkonto","4522.00","","",""
"2026-09-29","2026-09-29","Finanzamt Köln-Mitte","DE00370000000038001500","Debit Transfer","USt 08/2026","Hauptkonto","-812.40","","",""
"2026-09-30","2026-09-30","Café Kaffeebohne","","Presentment","","Hauptkonto","-3.80","","",""
"2026-09-30","2026-09-30","Café Kaffeebohne","","Presentment","","Hauptkonto","-3.80","","",""
"""

# Älteres Format
N26_ALT = """"Date","Payee","Account number","Transaction type","Payment reference","Amount (EUR)","Amount (Foreign Currency)","Type Foreign Currency","Exchange Rate"
"2026-09-03","OpenAI","","MasterCard Payment","","-17.32","-20.0","USD","1.1547"
"""

# Deutsche Variante mit Semikolon und Dezimalkomma
N26_DE = """Datum;Empfänger;Kontonummer;Transaktionstyp;Verwendungszweck;Betrag (EUR);Betrag (Fremdwährung);Fremdwährung;Wechselkurs
03.09.2026;OpenAI;;MasterCard Zahlung;;-17,32;-20,00;USD;1,1547
"""


def belege_september():
    return [
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.AUSLAND_13B, rechnungsdatum=date(2026, 9, 3), netto=D("20.00"),
              waehrung="USD", partner="OpenAI"),
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 9, 12),
              netto=D("50.00"), steuer=D("9.50"), partner="Hosting GmbH", rechnungsnummer="R-2026-0912"),
        Beleg(art=Art.AUSGANG, steuerfall=Steuerfall.INLAND, rechnungsdatum=date(2026, 9, 15), netto=D("3800.00"),
              partner="Beispiel AG", rechnungsnummer="2026-030"),
    ]


@pytest.mark.parametrize("text", [N26_ALT, N26_DE])
def test_formate(text):
    [u] = bank.n26_csv_lesen(text)
    assert u.buchungsdatum == date(2026, 9, 3)
    assert u.betrag == D("-17.32")
    assert u.partner == "OpenAI"
    assert (u.original_betrag, u.original_waehrung) == (D("-20.00"), "USD")


def test_aktuelles_format_und_doppelte_umsaetze():
    umsaetze = bank.n26_csv_lesen(N26_NEU)
    assert len(umsaetze) == 6
    assert umsaetze[2].betrag == D("4522.00")
    assert umsaetze[2].verwendungszweck == "Rechnung 2026-030"
    # zwei gleiche Kaffees am selben Tag bleiben zwei Umsätze
    assert umsaetze[4].pruefsumme != umsaetze[5].pruefsumme


def test_unbekanntes_format():
    with pytest.raises(bank.CsvFormatFehler):
        bank.n26_csv_lesen("foo,bar\n1,2\n")


def test_import_abgleich_und_ist_versteuerung(db, umrechner):
    for b in belege_september():
        db.beleg_anlegen(b)
    cfg = Config()
    e = bank.importieren(db, cfg, N26_NEU)
    assert (e.neu, e.zugeordnet, e.fehler) == (6, 3, [])

    umsaetze = db.alle_umsaetze()
    finanzamt = next(u for u in umsaetze if u.partner.startswith("Finanzamt"))
    assert finanzamt.ignoriert
    assert [u.partner for u in bank.ohne_beleg(umsaetze, "2026-09")] == ["Café Kaffeebohne"] * 2

    # Zahlungseingang setzt das Zahlungsdatum der Ausgangsrechnung -> zählt bei Ist-Versteuerung im September
    ausgang = next(b for b in db.alle_belege() if b.art == Art.AUSGANG)
    assert ausgang.zahlungsdatum == date(2026, 9, 28)
    assert berechnen(db.alle_belege(), 2026, 9, umrechner, "ist").wert("81") == D("3800")

    hinweis = bank.hinweis_fehlende_belege(umsaetze, "2026-09")
    assert hinweis.startswith("2 Kontoumsatz/-umsätze ohne Beleg (Ausgaben 7,60 €)")

    # erneuter Import legt nichts doppelt an
    assert bank.importieren(db, cfg, N26_NEU).vorhanden == 6


def test_kein_abgleich_bei_falschem_betrag_oder_richtung():
    [u] = bank.n26_csv_lesen(N26_NEU.splitlines()[0] + "\n" + N26_NEU.splitlines()[2].replace("-59.50", "-59.00"))
    belege = belege_september()
    for i, b in enumerate(belege):
        b.id = i + 1
    assert bank.kandidaten(u, belege, set()) == []
    u.betrag = D("59.50")  # Einnahme passt nicht zu einer Eingangsrechnung
    assert bank.kandidaten(u, belege, set()) == []


@pytest.fixture
def client(db, umrechner):
    return TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))


def test_web_ablauf(client, db):
    client.post("/api/belege", json={
        "art": "eingang", "steuerfall": "vorsteuer_inland", "rechnungsdatum": "2026-09-30", "netto": "3.19",
        "steuer": "0.61", "partner": "Café Kaffeebohne",
    })
    r = client.post("/api/import/bank", content=N26_NEU.encode(), headers={"Content-Type": "text/csv"}).json()
    assert r["neu"] == 6 and r["zugeordnet"] == 1

    bericht = client.get("/api/bericht/2026-09").json()
    assert len(bericht["umsaetze"]) == 6
    assert bericht["hinweise"][0]["stufe"] == "warnung"
    assert "4 Kontoumsatz/-umsätze ohne Beleg" in bericht["hinweise"][0]["text"]

    offen = [u for u in bericht["umsaetze"] if not u["beleg"] and not u["ignoriert"]]
    kaffee = next(u for u in offen if u["partner"].startswith("Café"))
    # Partner-Regel: der zweite Kaffee ohne Beleg wird ebenfalls akzeptiert
    client.post(f"/api/umsaetze/{kaffee['id']}/ignorieren", json={"regel": True, "grund": "privat"})
    assert db.bank_regeln() == {"café kaffeebohne": "privat"}

    hosting = next(u for u in offen if u["partner"] == "Hosting GmbH")
    beleg_id = client.post("/api/belege", json={
        "art": "eingang", "steuerfall": "vorsteuer_inland", "rechnungsdatum": "2026-09-12", "netto": "50",
        "steuer": "9.50", "partner": "Hosting GmbH",
    }).json()["id"]
    kandidaten = client.get(f"/api/umsaetze/{hosting['id']}/kandidaten").json()
    assert kandidaten[0]["id"] == beleg_id
    assert client.post(f"/api/umsaetze/{hosting['id']}/zuordnen", json={"beleg_id": beleg_id}).status_code == 200

    bericht = client.get("/api/bericht/2026-09").json()
    rest = [u["partner"] for u in bericht["umsaetze"] if not u["beleg"] and not u["ignoriert"]]
    assert rest == ["OpenAI", "Beispiel AG"]

    # Beleg löschen löst die Zuordnung
    client.delete(f"/api/belege/{beleg_id}")
    assert db.umsatz(hosting["id"]).beleg_id is None


# Kartenzahlung in USD, im Export nur der Euro-Betrag (Kursaufschlag der Bank)
N26_CURSOR = N26_NEU.splitlines()[0] + """
"2026-09-10","2026-09-10","CURSOR, AI POWERED IDE","","Presentment","","Hauptkonto","-18.00","","",""
"""


def _cursor_beleg(**felder) -> Beleg:
    werte = {"art": Art.EINGANG, "steuerfall": Steuerfall.AUSLAND_13B, "rechnungsdatum": date(2026, 9, 9),
             "netto": D("20.00"), "waehrung": "USD", "kurs": D("1.1513"), "partner": "Cursor"}
    return Beleg(**{**werte, **felder})


def test_fremdwaehrung_mit_kursdifferenz_wird_zugeordnet(client, db):
    beleg = db.beleg_anlegen(_cursor_beleg())
    r = client.post("/api/import/bank", content=N26_CURSOR.encode(), headers={"Content-Type": "text/csv"}).json()
    assert r["zugeordnet"] == 1

    [u] = client.get("/api/bericht/2026-09").json()["umsaetze"]
    assert u["beleg"]["id"] == beleg.id
    assert u["kosten_geldverkehr"] == "0.63"  # 18,00 € abgebucht, Beleg 20 USD / 1,1513 = 17,37 €


@pytest.mark.parametrize("felder", [
    {"partner": "Anderer Anbieter"},  # Betrag ungefähr, Name passt nicht
    {"netto": D("25.00")},  # Name passt, Betrag zu weit weg
])
def test_fremdwaehrung_ohne_sicheren_treffer_bleibt_offen(db, umrechner, felder):
    b = _cursor_beleg(**felder)
    b.id = 1
    [u] = bank.n26_csv_lesen(N26_CURSOR)
    euro = bank.euro_schaetzer(umrechner)
    assert bank.kandidaten(u, [b], set(), euro=euro) == []
    # zur Auswahl steht der Beleg trotzdem
    assert bank.kandidaten(u, [b], set(), nur_passende=False, euro=euro) == [b]


def test_fremdwaehrung_ohne_belegkurs_nutzt_monatskurs(umrechner):
    b = _cursor_beleg(kurs=None)  # 20 USD / 1,16 = 17,24 €
    [u] = bank.n26_csv_lesen(N26_CURSOR)
    euro = bank.euro_schaetzer(umrechner)
    assert bank.passt_betrag(u, b, euro) == "ungefaehr"
    assert bank.kosten_geldverkehr(u, b, euro) == D("0.76")
    assert bank.passt_betrag(u, b) is None  # ohne Kurs kein Vergleich


# Claude-Abo: Beleg mit Reverse Charge, Abbuchung am Folgetag
N26_ANTHROPIC = N26_NEU.splitlines()[0] + """
"2026-08-18","2026-08-18","ANTHROPIC* CLAUDE SUB","","Presentment","","Hauptkonto","-18.00","","",""
"2026-09-18","2026-09-18","ANTHROPIC* CLAUDE SUB","","Presentment","","Hauptkonto","-18.00","","",""
"""

ANTHROPIC = {"art": "eingang", "steuerfall": "eu_leistung_13b", "netto": "18.00", "steuersatz": "19",
             "partner": "Anthropic Ireland, Limited", "rechnungsnummer": "Invoice-9BF0758D-6257024"}


def test_beleg_ohne_datum_nachgetragen_wird_zugeordnet(client, db):
    client.post("/api/import/bank", content=N26_ANTHROPIC.encode(), headers={"Content-Type": "text/csv"})
    sept = next(u for u in db.alle_umsaetze() if u.buchungsdatum == date(2026, 9, 18))
    beleg = client.post("/api/belege", json={**ANTHROPIC, "rechnungsdatum": None}).json()
    assert db.umsatz(sept.id).beleg_id is None  # ohne Datum keine automatische Zuordnung ...
    kandidaten = client.get(f"/api/umsaetze/{sept.id}/kandidaten").json()
    assert beleg["id"] in [k["id"] for k in kandidaten]  # ... aber in der Auswahl

    client.put(f"/api/belege/{beleg['id']}", json={**ANTHROPIC, "rechnungsdatum": "2026-09-17"})
    assert db.umsatz(sept.id).beleg_id == beleg["id"]


def test_kandidaten_suche_und_schon_zugeordnete(client, db):
    client.post("/api/import/bank", content=N26_ANTHROPIC.encode(), headers={"Content-Type": "text/csv"})
    aug, sept = db.alle_umsaetze()
    beleg_id = client.post("/api/belege", json={**ANTHROPIC, "rechnungsdatum": "2026-09-17"}).json()["id"]
    for i in range(40):  # mehr Belege, als die Liste ohne Suche zeigt
        client.post("/api/belege", json={"art": "eingang", "steuerfall": "vorsteuer_inland", "netto": "10",
                                         "steuer": "1.90", "rechnungsdatum": "2026-09-01", "partner": f"Laden {i}"})
    assert db.umsatz(sept.id).beleg_id == beleg_id

    # Am August-Umsatz taucht der September-Beleg auf, ist aber als schon zugeordnet markiert.
    [k] = client.get(f"/api/umsaetze/{aug.id}/kandidaten", params={"suche": "anthropic"}).json()
    assert (k["id"], k["zugeordnet"]) == (beleg_id, "2026-09-18")
    [k] = client.get(f"/api/umsaetze/{aug.id}/kandidaten", params={"suche": "6257024 18,00"}).json()
    assert k["id"] == beleg_id

    treffer = client.get(f"/api/umsaetze/{aug.id}/kandidaten", params={"suche": "Laden 39"}).json()
    assert [k["partner"] for k in treffer] == ["Laden 39"]
    assert len(client.get(f"/api/umsaetze/{aug.id}/kandidaten").json()) == 30
