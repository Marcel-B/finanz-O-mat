import io
from datetime import date
from decimal import Decimal as D

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from ustva import belegdruck
from ustva.config import Config
from ustva.models import Beleg
from ustva.quellen.paperless import PaperlessClient
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app


def pdf_mit(seiten: int) -> bytes:
    w = PdfWriter()
    for _ in range(seiten):
        w.add_blank_page(width=200, height=200)
    puffer = io.BytesIO()
    w.write(puffer)
    return puffer.getvalue()


def beleg(art=Art.EINGANG, tag=10, **kw) -> Beleg:
    fall = Steuerfall.INLAND if art == Art.AUSGANG else Steuerfall.VORSTEUER_INLAND
    return Beleg(art=art, steuerfall=fall, rechnungsdatum=date(2026, 9, tag), zahlungsdatum=date(2026, 9, tag),
                 netto=D("10"), steuer=D("1.90"), **kw)


@pytest.fixture
def verzeichnis(tmp_path):
    (tmp_path / "ausgang").mkdir()
    (tmp_path / "ausgang" / "re-31.pdf").write_bytes(pdf_mit(1))
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / "telekom.xml").write_text("<Invoice/>")
    return tmp_path


def test_liste_und_pdf(verzeichnis):
    cfg = Config(rechnungs_verzeichnis=verzeichnis, paperless_url="http://paperless.local", paperless_token="t")
    belege = [
        beleg(tag=21, partner="Hetzner", quelle="paperless", quelle_ref="21", id=1),
        beleg(tag=21, partner="Hetzner", quelle="paperless", quelle_ref="21#2", id=2),  # zweiter Steuersatz
        beleg(tag=5, partner="Bäckerei", id=3),
        beleg(tag=8, partner="Telekom", quelle="verzeichnis", quelle_ref="x", quelle_link="eingang/telekom.xml", id=4),
        beleg(Art.AUSGANG, tag=12, partner="Kunde", quelle="verzeichnis", quelle_ref="y",
              quelle_link="ausgang/re-31.pdf", id=5),
    ]
    liste = belegdruck.liste(cfg, belege)
    assert [z["id"] for z in liste] == [5, 3, 4, 1, 2]  # Ausgang zuerst, dann nach Datum
    assert [z["grund"] for z in liste] == ["", "keine Datei hinterlegt", ".xml ohne PDF, bitte selbst drucken", "", ""]

    abrufe = []

    def handler(request):
        abrufe.append(request.url.path)
        return httpx.Response(200, content=pdf_mit(2), headers={"content-type": "application/pdf"})

    pl = PaperlessClient("http://paperless.local", "t", httpx.Client(transport=httpx.MockTransport(handler)))
    inhalt, enthalten, fehler = belegdruck.pdf(cfg, belege, pl)
    assert len(PdfReader(io.BytesIO(inhalt)).pages) == 3
    assert enthalten == [5, 1, 2] and fehler == []
    assert abrufe == ["/api/documents/21/download/"]  # dieselbe Datei nur einmal


def test_kaputter_beleg_wird_nicht_vermerkt(verzeichnis):
    (verzeichnis / "eingang" / "kaputt.pdf").write_bytes(b"%PDF-1.4 kaputt")
    cfg = Config(rechnungs_verzeichnis=verzeichnis)
    belege = [beleg(quelle="verzeichnis", quelle_ref="k", quelle_link="eingang/kaputt.pdf", id=1),
              beleg(Art.AUSGANG, quelle="verzeichnis", quelle_ref="y", quelle_link="ausgang/re-31.pdf", id=2)]
    _, enthalten, fehler = belegdruck.pdf(cfg, belege, None)
    assert enthalten == [2] and len(fehler) == 1 and fehler[0].startswith("2026-09-10_Eingang")


def test_endpunkte_vermerken_druck(verzeichnis, db, umrechner):
    client = TestClient(create_app(Config(rechnungs_verzeichnis=verzeichnis), db, umrechner,
                                   heute=date(2026, 10, 11)))
    kunde = db.beleg_anlegen(beleg(Art.AUSGANG, tag=12, partner="Kunde", quelle="verzeichnis", quelle_ref="y",
                                   quelle_link="ausgang/re-31.pdf"))
    db.beleg_anlegen(beleg(tag=5, partner="Bäckerei"))

    liste = client.get("/api/druck/2026-09").json()
    assert [(z["partner"], z["gedruckt"]) for z in liste] == [("Kunde", ""), ("Bäckerei", "")]

    r = client.post("/api/druck/2026-09/belege.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert 'inline; filename="Belege-2026-09.pdf"' in r.headers["content-disposition"]
    gedruckt = client.get(f"/api/belege/{kunde.id}").json()["gedruckt"]
    assert gedruckt.startswith("20")
    assert [z["gedruckt"] != "" for z in client.get("/api/druck/2026-09").json()] == [True, False]

    # Bearbeiten lässt den Vermerk stehen
    daten = client.get(f"/api/belege/{kunde.id}").json()
    eingabe = {k: daten[k] for k in ("art", "steuerfall", "rechnungsdatum", "zahlungsdatum", "netto", "steuersatz",
                                     "steuer", "waehrung", "partner", "beschreibung")}
    assert client.put(f"/api/belege/{kunde.id}", json={**eingabe, "partner": "Kunde AG"}).json()["gedruckt"] == gedruckt

    assert client.post("/api/druck/2026-09/belege.pdf?ids=999").status_code == 404
    assert client.post("/api/druck/2026-08/belege.pdf").status_code == 404
