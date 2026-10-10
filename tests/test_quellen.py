import json
from datetime import date
from decimal import Decimal as D

import httpx

from ustva.config import Config
from ustva.quellen import paperless, verzeichnis
from ustva.steuerfaelle import Art, Steuerfall
from ustva.waehrung import Umrechner, ezb_monatsmittel

from . import beispiele


def einfaches_pdf(zeilen: list[str]) -> bytes:
    """Minimales PDF mit Textebene (Helvetica, WinAnsi)."""
    befehle = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
    for z in zeilen:
        z = z.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        befehle.append(f"({z}) Tj T*")
    befehle.append("ET")
    inhalt = "\n".join(befehle).encode("cp1252")
    objekte = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(inhalt)).encode() + b" >>\nstream\n" + inhalt + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objekte, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objekte) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        pdf += f"{o:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(pdf)


def test_verzeichnis_import(tmp_path, db, umrechner):
    (tmp_path / "Ausgang" / "2026").mkdir(parents=True)
    (tmp_path / "eingang").mkdir()
    (tmp_path / "sonstiges").mkdir()
    (tmp_path / "Ausgang" / "2026" / "re-030.pdf").write_bytes(
        einfaches_pdf(beispiele.AUSGANG_INLAND.replace("€", "EUR").splitlines())
    )
    (tmp_path / "eingang" / "hosting.txt").write_text(beispiele.HOSTING_DE, encoding="utf-8")
    (tmp_path / "eingang" / "openai.txt").write_text(beispiele.OPENAI, encoding="utf-8")
    (tmp_path / "eingang" / "openai.txt.json").write_text(json.dumps({"kurs": "1.20", "geprueft": True}))
    (tmp_path / "sonstiges" / "notiz.txt").write_text("nichts")

    cfg = Config(rechnungs_verzeichnis=tmp_path, eigene_ustid="DE123456789")
    ergebnis = verzeichnis.importieren(db, cfg)
    assert (ergebnis.neu, ergebnis.uebersprungen, ergebnis.fehler) == (3, 1, [])

    belege = {b.beschreibung: b for b in db.alle_belege()}
    re030 = belege["re-030.pdf"]
    assert re030.art == Art.AUSGANG
    assert re030.steuerfall == Steuerfall.INLAND
    assert re030.netto == D("3800.00")
    assert re030.quelle_link == "Ausgang/2026/re-030.pdf"
    assert re030.pruefen

    openai = belege["openai.txt"]
    assert openai.steuerfall == Steuerfall.AUSLAND_13B
    assert openai.steuersatz == D("19")  # Reverse Charge: 19 % angenommen
    assert openai.kurs == D("1.20")
    assert not openai.pruefen

    # zweiter Lauf legt nichts doppelt an
    assert verzeichnis.importieren(db, cfg).vorhanden == 3


def _paperless_transport():
    dokumente = [
        {"id": 11, "title": "OpenAI September", "content": beispiele.OPENAI, "created": "2026-09-03",
         "correspondent": 1, "document_type": 1, "tags": [1], "custom_fields": [{"field": 1, "value": "USD20.00"}]},
        {"id": 12, "title": "Rechnung 2026-031", "content": beispiele.AUSGANG_EU,
         "created": "2026-09-30T00:00:00+02:00",
         "correspondent": 2, "document_type": 1, "tags": [2], "custom_fields": []},
        {"id": 13, "title": "Kontoauszug", "content": "Saldo 1.000,00", "created": "2026-09-30",
         "correspondent": None, "document_type": 2, "tags": [], "custom_fields": []},
    ]
    listen = {
        "/api/tags/": [{"id": 1, "name": "Eingangsrechnung"}, {"id": 2, "name": "Ausgangsrechnung"}],
        "/api/document_types/": [{"id": 1, "name": "Rechnung"}, {"id": 2, "name": "Kontoauszug"}],
        "/api/correspondents/": [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Acme B.V."}],
        "/api/custom_fields/": [{"id": 1, "name": "Netto", "data_type": "monetary"}],
    }
    anfragen = []

    def handler(request: httpx.Request) -> httpx.Response:
        anfragen.append(request)
        assert request.headers["Authorization"] == "Token geheim"
        if request.url.path == "/api/documents/":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"count": 3, "next": None, "results": dokumente[2:]})
            return httpx.Response(200, json={
                "count": 3, "next": "http://paperless.local/api/documents/?page=2", "results": dokumente[:2],
            })
        return httpx.Response(200, json={"count": 2, "next": None, "results": listen[request.url.path]})

    return httpx.MockTransport(handler), anfragen


def test_paperless_import(db):
    transport, anfragen = _paperless_transport()
    cfg = Config(paperless_url="http://paperless.local", paperless_token="geheim", eigene_ustid="DE123456789")
    ergebnis = paperless.importieren(db, cfg, 2026, 9, httpx.Client(transport=transport))
    assert (ergebnis.neu, ergebnis.uebersprungen, ergebnis.fehler) == (2, 1, [])

    doku = [a for a in anfragen if a.url.path == "/api/documents/"][0]
    assert doku.url.params["created__gte"] == "2026-07-01"
    assert doku.url.params["created__lt"] == "2026-10-01"

    belege = {b.quelle_ref: b for b in db.alle_belege()}
    openai = belege["11"]
    assert openai.partner == "OpenAI"
    assert openai.steuerfall == Steuerfall.AUSLAND_13B
    assert (openai.netto, openai.waehrung) == (D("20.00"), "USD")
    assert openai.quelle_link == "http://paperless.local/documents/11/details"
    acme = belege["12"]
    assert acme.art == Art.AUSGANG
    assert acme.steuerfall == Steuerfall.EU_LEISTUNG
    assert acme.rechnungsdatum == date(2026, 9, 30)


def test_paperless_falsches_token(db):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "Invalid token."}))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="falsch")
    ergebnis = paperless.importieren(db, cfg, 2026, 9, httpx.Client(transport=transport))
    assert ergebnis.neu == 0
    assert "Token" in ergebnis.fehler[0]


def test_geldfeld():
    assert paperless.geldfeld_parsen("EUR123.45") == (D("123.45"), "EUR")
    assert paperless.geldfeld_parsen("99.90") == (D("99.90"), None)
    assert paperless.geldfeld_parsen(None) is None


EZB_CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-01,1.1600
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-02,1.1700
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-03,1.1800
"""


def test_ezb_monatsmittel_und_speicher(db):
    aufrufe = []

    def handler(request):
        aufrufe.append(request)
        return httpx.Response(200, text=EZB_CSV)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert ezb_monatsmittel("USD", 2026, 9, client) == D("1.1700")
    assert aufrufe[0].url.params["endPeriod"] == "2026-09-30"

    u = Umrechner(db, client=client, heute=date(2026, 10, 10))
    assert u.monatskurs("USD", 2026, 9) == (D("1.1700"), "EZB-Monatsmittel")
    assert u.monatskurs("USD", 2026, 9) == (D("1.1700"), "EZB-Monatsmittel")
    assert len(aufrufe) == 2  # zweiter Aufruf kommt aus der Datenbank
    assert u.in_euro(D("117.00"), "USD", date(2026, 9, 3)) == (D("100.00"), D("1.1700"), "EZB-Monatsmittel")


def test_paperless_api_version_wird_ausgehandelt(db):
    """Aktuelle Paperless-Versionen kennen nur API 9 und 10 und lehnen alles andere mit 406 ab."""
    accept: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        kopf = request.headers["Accept"]
        accept.append(kopf)
        if "version=" in kopf and not kopf.endswith(("version=9", "version=10")):
            return httpx.Response(406, json={"detail": 'Invalid version in "Accept" header.'})
        kopfzeilen = {"X-Api-Version": "10", "X-Version": "2.20.0"}
        if request.url.path == "/api/tags/":
            return httpx.Response(200, headers=kopfzeilen,
                                  json={"next": None, "results": [{"id": 1, "name": "Eingangsrechnung"}]})
        return httpx.Response(200, headers=kopfzeilen, json={"next": None, "results": []})

    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    ergebnis = paperless.importieren(db, cfg, 2026, 9, httpx.Client(transport=httpx.MockTransport(handler)))
    assert ergebnis.fehler == []
    assert accept[0] == "application/json"
    assert set(accept[1:]) == {"application/json; version=10"}


def test_paperless_fehlertext_mit_detail(db):
    transport = httpx.MockTransport(
        lambda r: httpx.Response(406, json={"detail": 'Invalid version in "Accept" header.'})
    )
    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    ergebnis = paperless.importieren(db, cfg, 2026, 9, httpx.Client(transport=transport))
    assert ergebnis.fehler == ['Paperless antwortet mit Fehler 406: Invalid version in "Accept" header.']
