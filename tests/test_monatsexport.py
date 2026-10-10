import io
import zipfile
from datetime import date
from decimal import Decimal as D

import httpx
import pytest

from ustva import monatsexport
from ustva.berechnung import berechnen
from ustva.config import Config
from ustva.models import Beleg, Umsatz
from ustva.steuerfaelle import Art, Steuerfall

TAGS = [{"id": 1, "name": "Eingangsrechnung"}, {"id": 2, "name": "Kontoauszug"}, {"id": 3, "name": "UStVA-Protokoll"}]
DOKUMENTE = [
    {"id": 21, "title": "Hetzner", "created": "2026-09-21", "tags": [1], "content": ""},
    {"id": 30, "title": "Kontoauszug N26", "created": "2026-09-30", "tags": [2],
     "content": "Zeitraum 01.09.2026 bis 30.09.2026"},
    {"id": 31, "title": "Kontoauszug N26", "created": "2026-10-31", "tags": [2],
     "content": "Kontostand am 30.09.2026 ... 01.10.2026 bis 31.10.2026"},
    {"id": 40, "title": "Übertragungsprotokoll", "created": "2026-10-09", "tags": [3],
     "content": "Umsatzsteuer-Voranmeldung Zeitraum: September 2026"},
    {"id": 41, "title": "Übertragungsprotokoll", "created": "2026-09-08", "tags": [3],
     "content": "Umsatzsteuer-Voranmeldung Zeitraum: August 2026"},
]


def handler(request: httpx.Request) -> httpx.Response:
    pfad, p = request.url.path, request.url.params
    if pfad == "/api/tags/":
        return httpx.Response(200, json={"count": 3, "next": None, "results": TAGS})
    if pfad == "/api/documents/":
        treffer = [d for d in DOKUMENTE if p["created__gte"] <= d["created"] < p["created__lt"]
                   and int(p["tags__id__all"]) in d["tags"]]
        return httpx.Response(200, json={"count": len(treffer), "next": None, "results": treffer})
    if pfad.endswith("/download/"):
        dok_id = int(pfad.split("/")[3])
        return httpx.Response(200, content=f"PDF {dok_id}".encode(), headers={
            "content-type": "application/pdf", "content-disposition": f'attachment; filename="dok{dok_id}.pdf"'})
    return httpx.Response(404)


@pytest.fixture
def unterlagen(tmp_path, db, umrechner):
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / "strato.pdf").write_bytes(b"PDF Strato")
    cfg = Config(rechnungs_verzeichnis=tmp_path, paperless_url="http://paperless.local", paperless_token="t")
    neu = [
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 9, 21),
              netto=D("14.19"), steuer=D("2.70"), partner="Hetzner Online GmbH", quelle="paperless", quelle_ref="21"),
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 9, 6),
              netto=D("8.40"), steuer=D("1.60"), partner="STRATO", quelle="verzeichnis",
              quelle_link="eingang/strato.pdf"),
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 8, 28),
              netto=D("54.54"), steuer=D("10.36"), partner="Telekom", quelle="verzeichnis",
              quelle_link="eingang/fehlt.pdf"),
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 9, 2),
              netto=D("10"), steuer=D("1.90"), partner="Bäckerei / Café"),
    ]
    belege = [db.beleg_anlegen(b) for b in neu]
    umsaetze = [
        Umsatz(buchungsdatum=date(2026, 9, 4), betrag=D("-64.90"), partner="Telekom", beleg_id=belege[2].id),
        Umsatz(buchungsdatum=date(2026, 9, 30), betrag=D("-3.80"), partner="Kiosk"),
    ]
    bericht = berechnen(belege, 2026, 9, umrechner)
    inhalt = monatsexport.monats_zip(cfg, bericht, umsaetze, {b.id: b for b in belege},
                                     httpx.Client(transport=httpx.MockTransport(handler)))
    return zipfile.ZipFile(io.BytesIO(inhalt))


def test_zip_enthaelt_belege_auszug_und_protokoll(unterlagen):
    namen = sorted(unterlagen.namelist())
    assert namen == [
        "Belege/2026-09-06_Eingang_STRATO_10,00EUR.pdf",
        "Belege/2026-09-21_Eingang_Hetzner-Online-GmbH_16,89EUR.pdf",
        "FEHLT.txt",
        "Finanzamt/2026-10-09_Übertragungsprotokoll.pdf",
        "Kontoauszug/2026-09-30_Kontoauszug-N26.pdf",
        "UStVA-2026-09-Belege.csv",
        "UStVA-2026-09-Kennzahlen.csv",
    ]
    assert unterlagen.read("Belege/2026-09-06_Eingang_STRATO_10,00EUR.pdf") == b"PDF Strato"
    assert unterlagen.read("Finanzamt/2026-10-09_Übertragungsprotokoll.pdf") == b"PDF 40"


def test_fehlt_liste(unterlagen):
    fehlt = unterlagen.read("FEHLT.txt").decode()
    assert "Bäckerei-Café" in fehlt and "manuell angelegt" in fehlt
    assert "Telekom" in fehlt and "nicht mehr vorhanden" in fehlt  # Vormonat, über den Kontoumsatz dabei
    assert "Kontoumsatz 30.09.2026 Kiosk -3,80 € ohne Beleg" in fehlt


@pytest.mark.parametrize(("text", "erwartet"), [
    ("Zeitraum: September 2026", True), ("01.09.2026 - 30.09.2026", True), ("UStVA 09/2026", True),
    ("2026-09", True), ("Zeitraum: August 2026", False), ("19/2026", False), ("10.2026", False),
])
def test_monat_genannt(text, erwartet):
    assert monatsexport.monat_genannt(text, 2026, 9) is erwartet


def test_endpunkt_ohne_paperless(db, umrechner):
    from fastapi.testclient import TestClient

    from ustva.web import create_app

    client = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))
    r = client.get("/api/export/2026-09/unterlagen.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="Unterlagen-2026-09.zip"' in r.headers["content-disposition"]
    fehlt = zipfile.ZipFile(io.BytesIO(r.content)).read("FEHLT.txt").decode()
    assert "Kontoauszug: Paperless ist nicht eingerichtet" in fehlt
    assert client.get("/api/export/2026-09/kennzahlen.csv").status_code == 200
