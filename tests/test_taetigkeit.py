import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl.worksheet.table import Table

from ustva import taetigkeit
from ustva.config import Config
from ustva.models import Taetigkeit
from ustva.web import create_app


@pytest.fixture
def client(db, umrechner):
    return TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))


def vorlage_setzen(client, **werte):
    daten = {
        "titel": "Tätigkeitsnachweis für Kunde", "name": "Muster", "vorname": "Max",
        "kopf": ["Agentur GmbH", "Straße 1", "12345 Stadt"], "stundensatz": "100",
        "auftraege": [{"name": "Projekt A", "nummer": "PA-1"}, {"name": "Betrieb", "nummer": "OPS"}],
        "rollen": ["Entwickler"], "orte": ["Remote", "vor Ort"], "leistungsarten": ["Entwicklung", "Support"],
    } | werte
    r = client.put("/api/taetigkeiten/vorlage", json=daten)
    assert r.status_code == 200, r.text
    return r.json()


def eintrag(client, datum, stunden="8", **werte):
    daten = {"datum": datum, "stunden": stunden, "taetigkeit": "Feature gebaut", "auftrag": "Projekt A",
             "referenz": "4711", "rolle": "Entwickler", "ort": "Remote", "leistungsart": "Entwicklung"} | werte
    r = client.post("/api/taetigkeiten", json=daten)
    assert r.status_code == 201, r.text
    return r.json()


def test_vorlage_standard_und_speichern(client):
    v = client.get("/api/taetigkeiten/vorlage").json()
    assert v["stundensatz"] is None
    assert v["orte"] == ["Remote", "vor Ort"]
    v = vorlage_setzen(client, stundensatz="99,75")
    assert v["stundensatz"] == "99.75"
    assert client.get("/api/taetigkeiten/vorlage").json()["auftraege"][0] == {
        "name": "Projekt A", "nummer": "PA-1", "aktiv": True}
    assert client.put("/api/taetigkeiten/vorlage", json={"stundensatz": "-1"}).status_code == 400


def test_monat_mit_summen_vorschlag_und_luecken(client):
    vorlage_setzen(client)
    eintrag(client, "2026-10-01")
    eintrag(client, "2026-10-02", "6.5", taetigkeit="Bugfix", referenz="4712")
    eintrag(client, "2026-09-30", "8")

    m = client.get("/api/taetigkeiten?zeitraum=2026-10").json()
    assert [e["datum"] for e in m["eintraege"]] == ["2026-10-01", "2026-10-02"]
    assert m["stunden"] == "14.5"
    assert m["betrag"] == "1450.00"
    assert m["eintraege"][1]["betrag"] == "650.00"
    # 3.10. ist Feiertag, 4./5. Wochenende; heute ist der 10.10.
    assert m["vorschlag"]["datum"] == "2026-10-05"
    assert m["vorschlag"]["taetigkeit"] == "Bugfix"
    assert m["vorschlag"]["referenz"] == "4712"
    assert m["vorschlag"]["stunden"] == "8"
    assert m["fehlende_tage"] == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    assert m["texte"][0] == {"taetigkeit": "Bugfix", "auftrag": "Projekt A", "referenz": "4712"}
    assert {t["taetigkeit"] for t in m["texte"]} == {"Feature gebaut", "Bugfix"}


def test_vorschlag_im_leeren_monat_vom_letzten_eintrag(client):
    vorlage_setzen(client)
    eintrag(client, "2026-09-30", taetigkeit="Release vorbereitet")
    v = client.get("/api/taetigkeiten?zeitraum=2026-11").json()["vorschlag"]
    assert v["datum"] == "2026-11-02"  # 1.11. ist ein Sonntag
    assert v["taetigkeit"] == "Release vorbereitet"


def test_stundensatz_bleibt_am_eintrag(client, db):
    vorlage_setzen(client, stundensatz="90")
    e = eintrag(client, "2026-09-01")
    vorlage_setzen(client, stundensatz="100")
    assert e["satz"] == "90"
    r = client.put(f"/api/taetigkeiten/{e['id']}", json={"datum": "2026-09-01", "stunden": "4", "taetigkeit": "x"})
    assert r.json()["satz"] == "90"
    assert client.get("/api/taetigkeiten?zeitraum=2026-09").json()["betrag"] == "360.00"


def test_eingabe_wird_geprueft(client):
    r = client.post("/api/taetigkeiten", json={"datum": "2026-10-01", "stunden": "25"})
    assert r.status_code == 422
    assert client.delete("/api/taetigkeiten/999").status_code == 404
    e = eintrag(client, "2026-10-01")
    assert client.delete(f"/api/taetigkeiten/{e['id']}").status_code == 204
    assert client.get("/api/taetigkeiten?zeitraum=2026-10").json()["eintraege"] == []


def test_excel_wie_vorlage(client):
    vorlage_setzen(client)
    eintrag(client, "2026-10-02", "6.5", taetigkeit="Bugfix", auftrag="Betrieb", referenz="4712")
    eintrag(client, "2026-10-01", "8.25")
    r = client.get("/api/taetigkeiten/nachweis/2026-10")
    assert r.status_code == 200
    assert "Oktober%202026.xlsx" in r.headers["content-disposition"]

    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Oktober"]
    ws = wb["Oktober"]
    assert ws["A1"].value == "Tätigkeitsnachweis für Kunde"
    assert (ws["B3"].value, ws["B4"].value, ws["B5"].value) == ("Oktober", "Muster", "Max")
    assert ws["N2"].value == "Agentur GmbH"
    assert [c.value for c in ws[7]] == taetigkeit.SPALTEN
    zeile = [c.value for c in ws[8]]
    assert zeile[0] == datetime(2026, 10, 1)
    assert zeile[3:13] == ["PA-1", "Projekt A", "Muster, Max", "Entwickler", "Remote", 4711, "Entwicklung",
                           "Feature gebaut", 100, 8.25]
    assert zeile[13] == "=L8*M8"
    assert ws["D9"].value == "OPS"
    assert ws["A10"].value == "Gesamt"
    assert ws["M10"].value == "=SUM(M8:M9)"
    assert ws["N10"].value == "=SUM(N8:N9)"
    # berechnete Werte stehen mit in der Datei (Vorschau ohne Neuberechnung)
    werte = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True)["Oktober"]
    assert werte["M10"].value == 14.75
    assert werte["N10"].value == 1475
    assert werte["A14"].value == 14.75


def test_excel_ohne_eintraege(client):
    r = client.get("/api/taetigkeiten/nachweis/2026-12")
    ws = openpyxl.load_workbook(io.BytesIO(r.content))["Dezember"]
    assert ws["A9"].value == "Gesamt"
    assert ws["M9"].value == "=SUM(M8:M8)"


def bestehende_datei() -> bytes:
    """Nachbau der bisherigen Excel-Datei: Blatt "Werte" mit Tabellen, ein Blatt je Monat."""
    wb = openpyxl.Workbook()
    werte = wb.active
    werte.title = "Werte"
    for zeile in [["Leistungsart", "Ort", "Rolle"], ["Entwicklung", "Remote", "Entwickler"],
                  ["Betrieb", "vor Ort", "Product Owner"], ["Support", None, None]]:
        werte.append(zeile)
    werte.add_table(Table(displayName="Tabelle2", ref="A1:C4"))
    for i, zeile in enumerate([["Auftrag", "Auftragsnummer", "Beschreibung", "Epic"],
                               ["Projekt A", "PA-1", "Beschreibung", "1"], ["Betrieb", "OPS", "", "2"]]):
        for j, wert in enumerate(zeile):
            werte.cell(row=11 + i, column=1 + j, value=wert)
    werte.add_table(Table(displayName="Tabelle1", ref="A11:D13"))

    for monat, name in [(8, "August"), (9, "September"), (10, "Oktober")]:
        ws = wb.create_sheet(name)
        ws["A1"] = "Tätigkeitsnachweis für Kunde"
        ws["N2"], ws["N3"] = "Agentur GmbH", "Straße 1"
        ws["A3"], ws["B3"] = "Monat:", name
        ws["A4"], ws["A5"] = "Name:", "Vorname:"
        if monat != 10:
            ws["B4"], ws["B5"] = "Muster", "Max"
        for j, spalte in enumerate(taetigkeit.SPALTEN):
            ws.cell(row=7, column=1 + j, value=spalte)
        if monat != 10:
            for i, tag in enumerate([3, 4]):
                ws.append([datetime(2026, monat, tag), None, None, "=XLOOKUP()", "Projekt A", "Muster, Max",
                           "Entwickler", "Remote", 4711 + i, "Entwicklung", f"Aufgabe {i}", 99.75, 8 if i else 7.5,
                           f"=L{8 + i}*M{8 + i}"])
            # vorbereitete, aber leere Zeile
            ws.append([None, None, None, None, "Projekt A", None, "Entwickler", "Remote", None, "Entwicklung", None,
                       99.75, None])
        ws.cell(row=45, column=1, value="Gesamt")
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


def test_import_aus_bestehender_datei(client, db):
    r = client.post("/api/taetigkeiten/import", content=bestehende_datei())
    assert r.status_code == 200, r.text
    erg = r.json()
    assert erg["eintraege"] == 4
    assert erg["monate"] == ["2026-08", "2026-09"]
    assert "Stundensatz" in erg["vorlage"]

    v = client.get("/api/taetigkeiten/vorlage").json()
    assert v["titel"] == "Tätigkeitsnachweis für Kunde"
    assert (v["name"], v["vorname"]) == ("Muster", "Max")
    assert v["kopf"] == ["Agentur GmbH", "Straße 1"]
    assert v["stundensatz"] == "99.75"
    assert v["auftraege"] == [{"name": "Projekt A", "nummer": "PA-1", "aktiv": True},
                              {"name": "Betrieb", "nummer": "OPS", "aktiv": True}]
    assert v["leistungsarten"] == ["Entwicklung", "Betrieb", "Support"]
    assert v["orte"] == ["Remote", "vor Ort"]

    sept = db.taetigkeiten("2026-09")
    assert [(t.datum, t.stunden, t.referenz, t.taetigkeit) for t in sept] == [
        (date(2026, 9, 3), Decimal("7.5"), "4711", "Aufgabe 0"), (date(2026, 9, 4), Decimal("8"), "4712", "Aufgabe 1")]
    assert sept[0].satz == Decimal("99.75")

    # ein zweiter Import legt nichts doppelt an
    erg = client.post("/api/taetigkeiten/import", content=bestehende_datei()).json()
    assert erg["eintraege"] == 0
    assert erg["uebersprungen"] == ["2026-08", "2026-09"]
    assert len(db.taetigkeiten()) == 4


def test_excel_import_behaelt_vorhandene_auftraege(client):
    vorlage_setzen(client, auftraege=[{"name": "Projekt A", "nummer": "PA-1", "aktiv": False},
                                      {"name": "Eigener Auftrag", "nummer": "EA-9"}])
    r = client.post("/api/taetigkeiten/import", content=bestehende_datei())
    assert "1 neue Aufträge" in r.json()["vorlage"]
    v = client.get("/api/taetigkeiten/vorlage").json()
    assert [(a["name"], a["aktiv"]) for a in v["auftraege"]] == [
        ("Projekt A", False), ("Eigener Auftrag", True), ("Betrieb", True)]


def test_import_ohne_excel(client):
    r = client.post("/api/taetigkeiten/import", content=b"kein excel")
    assert r.status_code == 400
    assert "xlsx" in r.json()["detail"]


def test_zeilenhoehe_waechst_mit_text():
    assert taetigkeit._zeilenhoehe([("kurz", 42.5)]) == 30
    assert taetigkeit._zeilenhoehe([("x" * 200, 42.5)]) > 60


def test_betrag_ohne_satz():
    t = Taetigkeit(datum=date(2026, 10, 1), stunden=Decimal(8))
    assert taetigkeit.betrag(t, taetigkeit.Vorlage()) is None
    assert taetigkeit.betrag(t, taetigkeit.Vorlage(stundensatz=Decimal("99.75"))) == Decimal("798.00")


# Auftragsliste einfügen (nur erfundene Aufträge)

def test_auftragsliste_aus_excel_kopiert():
    text = ("Auftragsnummer\tBezeichnung\tStatus\n"
            "4500001234\tMusterportal Weiterentwicklung\tAktiv\n"
            "4500005678\tAltsystem Wartung\tInaktiv\n"
            "\n"
            "4500009999\tNeues Vorhaben\toffen\n")
    assert taetigkeit.auftraege_lesen(text) == [
        taetigkeit.Auftrag("Musterportal Weiterentwicklung", "4500001234", True),
        taetigkeit.Auftrag("Altsystem Wartung", "4500005678", False),
        taetigkeit.Auftrag("Neues Vorhaben", "4500009999", True),
    ]


def test_auftragsliste_csv_mit_gueltig_bis():
    text = ('Projekt;Nr.;Gültig bis\r\n"Kunde Muster, Phase 2";P-200;31.12.2026\r\n'
            "Kunde Muster, Phase 1;P-100;30.06.2026\r\n")
    assert taetigkeit.auftraege_lesen(text, heute=date(2026, 10, 29)) == [
        taetigkeit.Auftrag("Kunde Muster, Phase 2", "P-200", True),
        taetigkeit.Auftrag("Kunde Muster, Phase 1", "P-100", False),
    ]


def test_auftragsliste_ohne_kopfzeile():
    assert taetigkeit.auftraege_lesen("Beispielprojekt\tBP-01\tnein\nZweites Projekt\tBP-02\tja") == [
        taetigkeit.Auftrag("Beispielprojekt", "BP-01", False), taetigkeit.Auftrag("Zweites Projekt", "BP-02", True)]
    assert taetigkeit.auftraege_lesen("Nur ein Name\nNoch einer\n") == [
        taetigkeit.Auftrag("Nur ein Name"), taetigkeit.Auftrag("Noch einer")]


def test_auftragsliste_spalte_inaktiv_angekreuzt():
    text = "Auftrag\tAuftragsnummer\tInaktiv\nAlt\tA-1\tx\nNeu\tA-2\t\n"
    assert [a.aktiv for a in taetigkeit.auftraege_lesen(text)] == [False, True]


def test_auftragsliste_leer():
    with pytest.raises(ValueError):
        taetigkeit.auftraege_lesen("  \n\n")


def test_auftragsliste_ergaenzt_ohne_zu_ueberschreiben(client, db):
    vorlage_setzen(client)
    eintrag(client, "2026-10-01", auftrag="Projekt A")
    text = ("Auftrag\tAuftragsnummer\tStatus\n"
            "Projekt A (umbenannt)\tPA-1\tinaktiv\n"  # gleiche Nummer: bleibt wie in der Vorlage
            "betrieb\t\taktiv\n"  # gleicher Name ohne Nummer
            "Projekt C\tPC-3\tinaktiv\n"
            "Projekt D\tPD-4\taktiv\n")
    r = client.post("/api/taetigkeiten/auftraege", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json() == {"neu": ["Projekt C", "Projekt D"], "vorhanden": ["Projekt A", "Betrieb"],
                        "status_geaendert": [], "inaktiv": 1}
    v = client.get("/api/taetigkeiten/vorlage").json()
    assert v["auftraege"] == [
        {"name": "Projekt A", "nummer": "PA-1", "aktiv": True},
        {"name": "Betrieb", "nummer": "OPS", "aktiv": True},
        {"name": "Projekt C", "nummer": "PC-3", "aktiv": False},
        {"name": "Projekt D", "nummer": "PD-4", "aktiv": True},
    ]
    # erfasste Tätigkeiten bleiben unberührt, die Auftragsnummer im Nachweis auch
    assert [t.auftrag for t in db.taetigkeiten()] == ["Projekt A"]
    assert taetigkeit.vorlage_laden(db).auftragsnummer("Projekt A") == "PA-1"

    # zweites Einfügen legt nichts doppelt an; Status nur auf Wunsch
    r = client.post("/api/taetigkeiten/auftraege", json={"text": text, "status_uebernehmen": True}).json()
    assert r["neu"] == [] and r["status_geaendert"] == ["Projekt A"]
    v = client.get("/api/taetigkeiten/vorlage").json()
    assert len(v["auftraege"]) == 4
    assert v["auftraege"][0] == {"name": "Projekt A", "nummer": "PA-1", "aktiv": False}


def test_auftragsliste_fehler(client):
    r = client.post("/api/taetigkeiten/auftraege", json={"text": "\n"})
    assert r.status_code == 400


def test_vorschlag_nimmt_keinen_inaktiven_auftrag(client):
    vorlage_setzen(client, auftraege=[{"name": "Projekt A", "nummer": "PA-1", "aktiv": False},
                                      {"name": "Betrieb", "nummer": "OPS"}])
    assert client.get("/api/taetigkeiten?zeitraum=2026-10").json()["vorschlag"]["auftrag"] == "Betrieb"
    eintrag(client, "2026-10-01", auftrag="Projekt A")  # mit dem Schalter weiterhin möglich
    assert client.get("/api/taetigkeiten?zeitraum=2026-10").json()["vorschlag"]["auftrag"] == "Betrieb"
    eintrag(client, "2026-10-02", auftrag="Betrieb")
    assert client.get("/api/taetigkeiten?zeitraum=2026-10").json()["vorschlag"]["auftrag"] == "Betrieb"
