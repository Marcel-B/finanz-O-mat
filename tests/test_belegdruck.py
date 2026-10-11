import io
from datetime import date
from decimal import Decimal as D

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from ustva import belegdruck
from ustva.config import Config
from ustva.models import Beleg, Umsatz
from ustva.quellen.paperless import PaperlessClient
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app


def pdf_mit(seiten: int, schutz: bool = False) -> bytes:
    w = PdfWriter()
    for _ in range(seiten):
        w.add_blank_page(width=200, height=200)
    if schutz:  # Kopierschutz wie bei STRATO: ohne Passwort zu öffnen, AES-256
        w.encrypt(user_password="", algorithm="AES-256")
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
    assert enthalten == [5, 1, 2] and fehler == {}
    assert abrufe == ["/api/documents/21/download/"]  # dieselbe Datei nur einmal


def test_kaputter_beleg_wird_nicht_vermerkt(verzeichnis):
    (verzeichnis / "eingang" / "kaputt.pdf").write_bytes(b"%PDF-1.4 kaputt")
    cfg = Config(rechnungs_verzeichnis=verzeichnis)
    belege = [beleg(quelle="verzeichnis", quelle_ref="k", quelle_link="eingang/kaputt.pdf", id=1),
              beleg(Art.AUSGANG, quelle="verzeichnis", quelle_ref="y", quelle_link="ausgang/re-31.pdf", id=2)]
    _, enthalten, fehler = belegdruck.pdf(cfg, belege, None)
    assert enthalten == [2] and list(fehler) == [1]


def test_geschuetzte_und_verschobene_pdfs(verzeichnis):
    (verzeichnis / "eingang" / "strato.pdf").write_bytes(pdf_mit(1, schutz=True))
    (verzeichnis / "eingang" / "vorspann.pdf").write_bytes(b"\xef\xbb\xbf\r\n" + pdf_mit(2))
    cfg = Config(rechnungs_verzeichnis=verzeichnis)
    belege = [beleg(quelle="verzeichnis", quelle_ref="s", quelle_link="eingang/strato.pdf", id=1),
              beleg(quelle="verzeichnis", quelle_ref="v", quelle_link="eingang/vorspann.pdf", id=2)]
    inhalt, enthalten, fehler = belegdruck.pdf(cfg, belege, None)
    assert enthalten == [1, 2] and fehler == {}
    assert len(PdfReader(io.BytesIO(inhalt)).pages) == 3


def test_paperless_original_wenn_archiv_unbrauchbar():
    def handler(request):
        if request.url.params.get("original") == "true":
            return httpx.Response(200, content=pdf_mit(1), headers={"content-type": "application/pdf"})
        return httpx.Response(200, content=b"kaputt", headers={"content-type": "application/pdf"})

    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    pl = PaperlessClient("http://paperless.local", "t", httpx.Client(transport=httpx.MockTransport(handler)))
    belege = [beleg(quelle="paperless", quelle_ref="7", id=1), beleg(quelle="paperless", quelle_ref="7#2", id=2)]
    _, enthalten, fehler = belegdruck.pdf(cfg, belege, pl)
    assert enthalten == [1, 2] and fehler == {}


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


def test_fehler_in_liste_und_einzeldruck(verzeichnis, db, umrechner):
    (verzeichnis / "eingang" / "kaputt.pdf").write_bytes(b"%PDF-1.4 kaputt")
    client = TestClient(create_app(Config(rechnungs_verzeichnis=verzeichnis), db, umrechner,
                                   heute=date(2026, 10, 11)))
    kaputt = db.beleg_anlegen(beleg(partner="Kaputt", quelle="verzeichnis", quelle_ref="k",
                                    quelle_link="eingang/kaputt.pdf"))
    zweiter = db.beleg_anlegen(beleg(partner="Kaputt", quelle="verzeichnis", quelle_ref="k#2",
                                     quelle_link="eingang/kaputt.pdf"))
    manuell = db.beleg_anlegen(beleg(partner="Bäckerei"))

    assert client.post("/api/druck/2026-09/belege.pdf").status_code == 404
    liste = {z["id"]: z for z in client.get("/api/druck/2026-09").json()}
    assert liste[kaputt.id]["fehler"] and liste[zweiter.id]["fehler"] and not liste[manuell.id]["fehler"]

    # einzeln geht die Datei unverändert an den Browser, beide Belege der Datei gelten als gedruckt
    r = client.post(f"/api/belege/{kaputt.id}/drucken")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 kaputt"
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    liste = {z["id"]: z for z in client.get("/api/druck/2026-09").json()}
    assert liste[kaputt.id]["gedruckt"] and liste[zweiter.id]["gedruckt"] and not liste[kaputt.id]["fehler"]
    assert not liste[manuell.id]["gedruckt"]

    assert client.post(f"/api/belege/{manuell.id}/drucken").status_code == 404
    assert client.post("/api/belege/999/drucken").status_code == 404


def test_beleg_aus_anderem_monat_kommt_nicht_in_den_druck(verzeichnis, db, umrechner):
    """Eine Juli-Rechnung, im August abgebucht, gehört in den Juli-Druck, nicht in den August-Druck."""
    client = TestClient(create_app(Config(rechnungs_verzeichnis=verzeichnis), db, umrechner,
                                   heute=date(2026, 10, 11)))
    juli = db.beleg_anlegen(Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND,
                                  rechnungsdatum=date(2026, 7, 29), netto=D("10"), steuer=D("1.90"),
                                  partner="Software", quelle="verzeichnis", quelle_ref="y",
                                  quelle_link="ausgang/re-31.pdf"))
    db.umsatz_anlegen(Umsatz(buchungsdatum=date(2026, 8, 3), betrag=D("-11.90"), partner="Software",
                             beleg_id=juli.id, pruefsumme="software"))

    assert client.get("/api/druck/2026-08").json() == []
    assert client.post("/api/druck/2026-08/belege.pdf").status_code == 404
    assert client.get(f"/api/belege/{juli.id}").json()["gedruckt"] == ""
    assert [z["id"] for z in client.get("/api/druck/2026-07").json()] == [juli.id]
