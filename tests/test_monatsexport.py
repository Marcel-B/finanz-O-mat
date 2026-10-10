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
from ustva.quellen.paperless import PaperlessClient
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
        # Zählt in der Voranmeldung 09/2026, abgebucht wird erst im Oktober
        Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 9, 22),
              netto=D("52.85"), steuer=D("10.04"), partner="Telekom Deutschland GmbH", quelle="paperless",
              quelle_ref="22"),
    ]
    belege = [db.beleg_anlegen(b) for b in neu]
    umsaetze = [
        Umsatz(buchungsdatum=date(2026, 9, 4), betrag=D("-64.90"), partner="Telekom", beleg_id=belege[2].id),
        Umsatz(buchungsdatum=date(2026, 9, 30), betrag=D("-3.80"), partner="Kiosk"),
        Umsatz(buchungsdatum=date(2026, 9, 15), betrag=D("-42.00"), partner="Rewe", ignoriert=True,
               ignoriert_grund="Fehlbuchung/privat: privater Einkauf, Karte verwechselt"),
        Umsatz(buchungsdatum=date(2026, 9, 29), betrag=D("-812.40"), partner="Finanzamt", ignoriert=True,
               ignoriert_grund="Stichwort „Finanzamt“"),
    ]
    oktober = [Umsatz(buchungsdatum=date(2026, 10, 1), betrag=D("-62.89"), partner="Telekom",
                      beleg_id=belege[4].id)]
    bericht = berechnen(belege, 2026, 9, umrechner)
    assert belege[4].id in {z.beleg.id for z in bericht.zeilen}
    inhalt = monatsexport.monats_zip(cfg, bericht, umsaetze, {b.id: b for b in belege},
                                     httpx.Client(transport=httpx.MockTransport(handler)), andere_umsaetze=oktober)
    return zipfile.ZipFile(io.BytesIO(inhalt))


def test_zip_enthaelt_belege_auszug_und_protokoll(unterlagen):
    namen = sorted(unterlagen.namelist())
    assert namen == [
        "Belege/2026-09-06_Eingang_STRATO_10,00EUR.pdf",
        "Belege/2026-09-21_Eingang_Hetzner-Online-GmbH_16,89EUR.pdf",
        "Belege/2026-09-22_Eingang_Telekom-Deutschland-GmbH_62,89EUR.pdf",
        "Finanzamt/2026-10-09_Übertragungsprotokoll.pdf",
        "INFO.txt",
        "Kontoauszug/2026-09-30_Kontoauszug-N26.pdf",
        "UStVA-2026-09-Belege.csv",
        "UStVA-2026-09-Kennzahlen.csv",
    ]
    assert unterlagen.read("Belege/2026-09-06_Eingang_STRATO_10,00EUR.pdf") == b"PDF Strato"
    assert unterlagen.read("Finanzamt/2026-10-09_Übertragungsprotokoll.pdf") == b"PDF 40"


def test_info_datei(unterlagen):
    info = unterlagen.read("INFO.txt").decode()
    fehlt, rest = info.split("Fehlbuchungen / privat", 1)
    assert "Bäckerei-Café" in fehlt and "manuell angelegt" in fehlt
    assert "Telekom" in fehlt and "nicht mehr vorhanden" in fehlt  # Vormonat, über den Kontoumsatz dabei
    assert "- Kontoumsatz 30.09.2026 Kiosk -3,80 € ohne Beleg" in fehlt
    assert ("In der Voranmeldung enthalten, aber in einem anderen Monat abgebucht" in fehlt
            and "- 2026-09-22_Eingang_Telekom-Deutschland-GmbH_62,89EUR: abgebucht am 01.10.2026" in fehlt)
    fehlbuchungen, ohne_beleg = rest.split("kein Beleg nötig", 1)
    assert "- Kontoumsatz 15.09.2026 Rewe -42,00 €: privater Einkauf, Karte verwechselt" in fehlbuchungen
    assert "- Kontoumsatz 29.09.2026 Finanzamt -812,40 € (Stichwort „Finanzamt“)" in ohne_beleg


@pytest.mark.parametrize(("text", "erwartet"), [
    ("Zeitraum: September 2026", True), ("01.09.2026 - 30.09.2026", True), ("UStVA 09/2026", True),
    ("2026-09", True), ("Zeitraum: August 2026", False), ("19/2026", False), ("10.2026", False),
    # ELSTER-Protokoll, wie Paperless es ablegt
    ("117_5020_4819_Uebertragungsprotokoll_UStVA_2026_-_September", True),
    ("Umsatzsteuer-Voranmeldung\nAllgemein\nJahr 2026\nZeitraum September\nSteuernummer 117/5020/4819", True),
    ("Jahr 2026 Zeitraum Oktober Erstellungsdatum 10.11.2026", False),
    ("statement_Sep_2026.pdf", True), ("Septembermond 2026", False),
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
    fehlt = zipfile.ZipFile(io.BytesIO(r.content)).read("INFO.txt").decode()
    assert "Kontoauszug: Paperless ist nicht eingerichtet" in fehlt
    assert client.get("/api/export/2026-09/kennzahlen.csv").status_code == 200


def test_protokoll_ohne_monat_im_text_aus_dem_folgemonat(db, umrechner):
    alt = list(DOKUMENTE)
    DOKUMENTE[:] = [
        {"id": 50, "title": "Scan Finanzamt", "created": "2026-10-10", "tags": [3], "content": "Übertragungsprotokoll"},
        {"id": 51, "title": "Scan Finanzamt", "created": "2026-10-11", "tags": [3], "content": "Zeitraum August 2026"},
    ]
    try:
        cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
        inhalt = monatsexport.monats_zip(cfg, berechnen([], 2026, 9, umrechner), [], {},
                                         httpx.Client(transport=httpx.MockTransport(handler)))
    finally:
        DOKUMENTE[:] = alt
    namen = zipfile.ZipFile(io.BytesIO(inhalt)).namelist()
    assert [n for n in namen if n.startswith("Finanzamt/")] == ["Finanzamt/2026-10-10_Scan-Finanzamt.pdf"]


def test_protokoll_vorhanden():
    pl = PaperlessClient("http://paperless.local", "t", httpx.Client(transport=httpx.MockTransport(handler)))
    assert monatsexport.protokoll_vorhanden(pl, "UStVA-Protokoll", 2026, 9)
    assert monatsexport.protokoll_vorhanden(pl, "UStVA-Protokoll", 2026, 8)
    assert not monatsexport.protokoll_vorhanden(pl, "UStVA-Protokoll", 2026, 10)
    assert not monatsexport.protokoll_vorhanden(pl, "Gibt-es-nicht", 2026, 9)


def test_uebermittlungsdatum_macht_kein_protokoll_fuer_den_folgemonat():
    # Das Protokoll für September nennt mit "09.10.2026" auch den Oktober
    alt = list(DOKUMENTE)
    DOKUMENTE[:] = [{"id": 60, "title": "117_5020_4819_Uebertragungsprotokoll_UStVA_2026_-_September",
                     "created": "2026-10-09", "tags": [3], "content": "Übermittelt am 09.10.2026 um 18:02"}]
    try:
        pl = PaperlessClient("http://paperless.local", "t", httpx.Client(transport=httpx.MockTransport(handler)))
        assert monatsexport.protokoll_vorhanden(pl, "UStVA-Protokoll", 2026, 9)
        assert not monatsexport.protokoll_vorhanden(pl, "UStVA-Protokoll", 2026, 10)
    finally:
        DOKUMENTE[:] = alt


def test_status_zeigt_naechsten_monat_wenn_protokoll_da(db, umrechner, monkeypatch):
    from fastapi.testclient import TestClient

    from ustva.web import create_app

    echt = httpx.Client
    monkeypatch.setattr("ustva.web.httpx.Client", lambda **kw: echt(transport=httpx.MockTransport(handler)))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    frist = TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 10))).get("/api/status").json()["frist"]
    assert frist["zeitraum"] == "2026-10" and frist["faellig_text"] == "Dienstag, 10.11.2026"
    assert frist["abgegeben"] == ["September 2026"]


def test_status_ohne_erreichbares_paperless_wie_bisher(db, umrechner, monkeypatch):
    from fastapi.testclient import TestClient

    from ustva.web import create_app

    def kaputt(request):
        raise httpx.ConnectError("nicht erreichbar")

    echt = httpx.Client
    monkeypatch.setattr("ustva.web.httpx.Client", lambda **kw: echt(transport=httpx.MockTransport(kaputt)))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    frist = TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 10))).get("/api/status").json()["frist"]
    assert frist["zeitraum"] == "2026-09" and frist["abgegeben"] == []
