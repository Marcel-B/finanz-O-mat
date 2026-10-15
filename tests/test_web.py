from datetime import date

import pytest
from fastapi.testclient import TestClient

from ustva.config import Config
from ustva.web import create_app


@pytest.fixture
def client(db, umrechner, tmp_path):
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / "rechnung.txt").write_text("Rechnungsdatum 05.09.2026\nSumme netto 10,00\nMwSt 19 % 1,90")
    cfg = Config(rechnungs_verzeichnis=tmp_path)
    return TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 10)))


def test_status_zeigt_frist(client):
    s = client.get("/api/status").json()
    assert s["frist"]["zeitraum"] == "2026-09"
    assert s["frist"]["faellig_text"] == "Montag, 12.10.2026"
    assert s["versteuerung"] == "ist"


def test_beleg_anlegen_und_bericht(client):
    r = client.post("/api/belege", json={
        "art": "ausgang", "steuerfall": "inland", "rechnungsdatum": "2026-09-15", "netto": "1000.00",
        "steuersatz": "19", "partner": "Kunde GmbH",
    })
    assert r.status_code == 201
    beleg_id = r.json()["id"]

    # Ist-Versteuerung (Standard): ohne Zahlungsdatum zählt die Rechnung noch nicht
    bericht = client.get("/api/bericht/2026-09").json()
    assert bericht["zahllast"] == "0"
    assert any("ohne Zahlungsdatum" in h["text"] for h in bericht["hinweise"])
    assert [b["id"] for b in client.get("/api/offen").json()] == [beleg_id]

    r = client.put(f"/api/belege/{beleg_id}", json={
        "art": "ausgang", "steuerfall": "inland", "rechnungsdatum": "2026-09-15", "netto": "1000.00",
        "steuersatz": "19", "zahlungsdatum": "2026-09-28",
    })
    assert r.status_code == 200

    bericht = client.get("/api/bericht/2026-09").json()
    kz = {k["id"]: k["betrag"] for k in bericht["kennzahlen"]}
    assert kz["81"] == "1000"
    assert kz["81-steuer"] == "190.00"
    assert bericht["zahllast"] == "190.00"

    r = client.put(f"/api/belege/{beleg_id}", json={
        "art": "ausgang", "steuerfall": "inland", "rechnungsdatum": "2026-09-15", "netto": "1000.00",
        "steuersatz": "7", "zahlungsdatum": "2026-09-28",
    })
    assert r.status_code == 200
    assert client.get("/api/bericht/2026-09").json()["zahllast"] == "70.00"

    assert client.delete(f"/api/belege/{beleg_id}").status_code == 204
    assert client.get("/api/bericht/2026-09").json()["zahllast"] == "0"


def test_ungueltige_eingaben(client):
    assert client.get("/api/bericht/2026-13").status_code == 400
    r = client.post("/api/belege", json={"art": "ausgang", "steuerfall": "inland", "netto": "1", "waehrung": "Dollar"})
    assert r.status_code == 422


def test_import_und_export(client):
    r = client.post("/api/import", json={"zeitraum": "2026-09"}).json()
    assert r["verzeichnis"]["neu"] == 1
    bericht = client.get("/api/bericht/2026-09").json()
    assert bericht["belege"][0]["pruefen"] is True
    beleg_id = bericht["belege"][0]["id"]
    assert client.get(f"/api/belege/{beleg_id}/datei").status_code == 200
    client.post(f"/api/belege/{beleg_id}/geprueft")
    assert client.get("/api/bericht/2026-09").json()["belege"][0]["pruefen"] is False

    csv = client.get("/api/export/2026-09/kennzahlen.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    text = csv.content.decode("utf-8-sig")
    assert "2026-09;66;Vorsteuer aus Rechnungen anderer Unternehmer;1,90;" in text
    belege = client.get("/api/export/2026-09/belege.csv").content.decode("utf-8-sig")
    assert belege.splitlines()[1].startswith("05.09.2026;;Eingang;")


def test_pruefung_zuruecknehmen(client):
    beleg_id = client.post("/api/belege", json={"art": "eingang", "steuerfall": "vorsteuer_inland",
                                                 "rechnungsdatum": "2026-09-05", "netto": "10"}).json()["id"]
    assert client.get(f"/api/belege/{beleg_id}").json()["pruefen"] is False
    r = client.delete(f"/api/belege/{beleg_id}/geprueft")
    assert r.status_code == 200 and r.json()["pruefen"] is True
    assert client.get(f"/api/belege/{beleg_id}").json()["pruefen"] is True
    assert client.post(f"/api/belege/{beleg_id}/geprueft").json()["pruefen"] is False
    assert client.delete("/api/belege/9999/geprueft").status_code == 404


def test_offene_belege(client):
    client.post("/api/belege", json={"art": "eingang", "steuerfall": "vorsteuer_inland", "netto": "5"})
    assert len(client.get("/api/offen").json()) == 1


def test_kurse(client):
    r = client.put("/api/kurse", json={"waehrung": "gbp", "zeitraum": "2026-09", "kurs": "0.86"})
    assert {"waehrung": "GBP", "zeitraum": "2026-09", "kurs": "0.86", "quelle": "BMF"} in r.json()
    assert client.delete("/api/kurse/GBP/2026-09").status_code == 204


def test_startseite(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Finanz-O-Mat" in r.text


def test_sicherheits_koepfe(client):
    seite = client.get("/")
    assert "frame-ancestors 'self'" in seite.headers["content-security-policy"]
    assert seite.headers["x-frame-options"] == "SAMEORIGIN"
    api = client.get("/api/status")
    assert api.headers["cache-control"] == "no-store"
    assert api.headers["x-content-type-options"] == "nosniff"


def test_fremde_seite_darf_nichts_aendern(client):
    beleg = {"art": "ausgang", "steuerfall": "inland", "rechnungsdatum": "2026-09-15", "netto": "1",
             "steuersatz": "19"}
    r = client.post("/api/belege", json=beleg, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert client.post("/api/belege", json=beleg, headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.post("/api/belege", json=beleg, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 201
    # Lesen und Links von außen bleiben möglich
    assert client.get("/api/status", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_uebersicht_api(client):
    client.post("/api/belege", json={
        "art": "ausgang", "steuerfall": "inland", "rechnungsdatum": "2026-09-15", "netto": "1000.00",
        "steuersatz": "19", "partner": "Kunde GmbH", "zahlungsdatum": "2026-09-20",
    })
    d = client.get("/api/uebersicht/2026").json()
    assert d["versteuerung"] == "ist"
    assert d["monate"][8]["einnahmen"] == "1000.00"
    assert d["summe"]["zahllast"] == "190.00"

    r = client.get("/api/uebersicht/2026/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="uebersicht-2026.csv"'
    assert "2026-09;1000,00;0,00;1000,00;190,00;1" in r.text
    assert client.get("/api/uebersicht/1999").status_code == 400


def test_startseite_ohne_gebaute_oberflaeche(db, umrechner, monkeypatch, tmp_path):
    monkeypatch.setattr("ustva.web.STATIC", tmp_path)
    c = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))
    r = c.get("/")
    assert r.status_code == 200
    assert "npm run build" in r.text
