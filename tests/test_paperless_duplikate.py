import json
from datetime import date
from decimal import Decimal as D

import httpx

from ustva import paperless_duplikate as pd
from ustva.__main__ import duplikate_befehl
from ustva.config import Config
from ustva.models import Beleg
from ustva.quellen.paperless import PaperlessClient
from ustva.steuerfaelle import Art, Steuerfall

SEVDESK = """sevDesk GmbH, Im Unteren Angel 1, 77652 Offenburg
Rechnung
Rechnungsnummer: 92026-313716
Rechnungsdatum: 11.09.2026
Buchhaltung 1 Monat
Netto 25,90 EUR
MwSt. 19% 4,92 EUR
Gesamtbetrag 30,82 EUR"""


def telekom(nummer: str) -> str:
    return f"""Telekom Deutschland GmbH
Mobilfunk-Rechnung für August 2026
Rechnungsnummer: {nummer}
Datum: 04.09.2026
Netto 54,54 EUR
USt 19% 10,36 EUR
Rechnungsbetrag 64,90 EUR"""


def dok(i: int, titel: str, inhalt: str, tags: list[int], korrespondent: int | None = 1) -> dict:
    return {"id": i, "title": titel, "content": inhalt, "created": "2026-09-14", "added": f"2026-10-{i:02d}",
            "correspondent": korrespondent, "document_type": None, "tags": tags, "custom_fields": []}


DOKUMENTE = [
    dok(1, "Rechnung-92026-313716", SEVDESK, [1, 3]),
    dok(2, "92026-313716", SEVDESK.replace("\n", "  \n"), [1, 3]),  # andere Datei, leicht anderer OCR-Text
    dok(3, "Deine sevdesk-Rechnung", SEVDESK, [1, 3, 4]),  # aus der Mail
    dok(4, "Telekom Juli", telekom("3818177900041"), [1], 2),
    dok(5, "Telekom zweiter Vertrag", telekom("3818177900099"), [1], 2),  # gleicher Betrag, andere Nummer
    dok(6, "Kontoauszug", "Kontoauszug September " * 10, [2]),
    dok(7, "Kontoauszug Kopie", "Kontoauszug September " * 10, [2]),  # kein Rechnungs-Tag: bleibt außen vor
]
TAGS = [{"id": 1, "name": "Eingangsrechnung"}, {"id": 2, "name": "Kontoauszug"},
        {"id": 3, "name": "b-velop"}, {"id": 4, "name": "E-Mail"}]


def transport(anfragen: list):
    listen = {"/api/tags/": TAGS, "/api/document_types/": [], "/api/custom_fields/": [],
              "/api/correspondents/": [{"id": 1, "name": "sevDesk"}, {"id": 2, "name": "Telekom Deutschland GmbH"}],
              "/api/documents/": DOKUMENTE}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            anfragen.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"count": 1, "next": None, "results": listen[request.url.path]})

    return httpx.MockTransport(handler)


def client(anfragen: list) -> PaperlessClient:
    return PaperlessClient("http://paperless.local", "geheim", httpx.Client(transport=transport(anfragen)))


def cfg() -> Config:
    return Config(paperless_url="http://paperless.local", paperless_token="geheim")


def test_findet_dieselbe_rechnung_in_verschiedenen_dateien(db):
    gruppen = pd.finden(client([]), cfg(), db)
    assert len(gruppen) == 1
    g = gruppen[0]
    assert g.behalten["id"] == 1
    assert [d["id"] for d in g.loeschen] == [2, 3]
    assert g.tags_dazu == [4]  # E-Mail-Tag wandert auf das behaltene Dokument
    assert not g.hinweis


def test_behaelt_das_dokument_mit_beleg_in_finanzomat(db):
    db.beleg_anlegen(Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, netto=D("25.90"),
                           steuersatz=D(19), rechnungsdatum=date(2026, 9, 11), quelle="paperless", quelle_ref="3"))
    g = pd.finden(client([]), cfg(), db)[0]
    assert g.behalten["id"] == 3
    assert [d["id"] for d in g.loeschen] == [1, 2]
    assert g.tags_dazu == []


def test_zwei_belege_in_finanzomat_werden_nicht_angefasst(db):
    for ref in ("1", "2"):
        db.beleg_anlegen(Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, netto=D("25.90"),
                               steuersatz=D(19), rechnungsdatum=date(2026, 9, 11), quelle="paperless", quelle_ref=ref))
    anfragen = []
    pl = client(anfragen)
    g = pd.finden(pl, cfg(), db)[0]
    assert "erst dort" in g.hinweis.lower()
    pd.aufraeumen(pl, g)
    assert anfragen == []


def test_aufraeumen_uebertraegt_tags_und_loescht(db):
    anfragen = []
    pl = client(anfragen)
    pd.aufraeumen(pl, pd.finden(pl, cfg(), db)[0])
    assert anfragen == [
        ("PATCH", "/api/documents/1/", {"tags": [1, 3, 4]}),
        ("POST", "/api/documents/bulk_edit/", {"documents": [2, 3], "method": "delete", "parameters": {}}),
    ]


class Args:
    von = None
    loeschen = True
    ja = False


def test_befehl_loescht_nur_nach_bestaetigung(db, capsys):
    anfragen = []
    assert duplikate_befehl(Args(), cfg(), db, eingabe=lambda _: "n", pl=client(anfragen)) == 0
    assert anfragen == []
    ausgabe = capsys.readouterr().out
    assert "behalten  #1" in ausgabe and "löschen   #3" in ausgabe and "Nichts gelöscht" in ausgabe
    assert duplikate_befehl(Args(), cfg(), db, eingabe=lambda _: "j", pl=client(anfragen)) == 0
    assert [a[0] for a in anfragen] == ["PATCH", "POST"]


def test_folgt_umleitung_und_schreibt_an_die_neue_adresse(db):
    anfragen = []
    innen = transport(anfragen)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "http":
            return httpx.Response(301, headers={"Location": str(request.url.copy_with(scheme="https"))})
        return innen.handle_request(request)

    pl = PaperlessClient("http://paperless.local", "geheim", httpx.Client(transport=httpx.MockTransport(handler)))
    pd.aufraeumen(pl, pd.finden(pl, cfg(), db)[0])
    assert pl.url == "https://paperless.local"
    assert [a[:2] for a in anfragen] == [("PATCH", "/api/documents/1/"), ("POST", "/api/documents/bulk_edit/")]
