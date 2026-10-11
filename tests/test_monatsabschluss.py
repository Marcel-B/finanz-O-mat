import json
from datetime import date
from decimal import Decimal as D

import httpx
import pytest
from fastapi.testclient import TestClient

from ustva import monatsabschluss
from ustva.berechnung import berechnen
from ustva.config import Config
from ustva.models import Beleg, Umsatz
from ustva.quellen.paperless import PaperlessClient
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app

from .test_monatsexport import handler as paperless_mit_dokumenten

PDF = b"%PDF-1.7 Kontoauszug"
PAPERLESS = Config(paperless_url="http://paperless.local", paperless_token="t")


@pytest.fixture
def client(db, umrechner):
    return TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 11)))


def test_mail_vorlage(client):
    stand = client.get("/api/monatsabschluss/2026-09").json()
    assert stand["mail"] == monatsabschluss.MAIL_STANDARD
    neu = {"an": "steuer@example.org", "betreff": "Unterlagen {monat} {jahr}", "text": "Hallo,\n\nanbei {monat}."}
    assert client.put("/api/monatsabschluss/mail", json=neu).json() == neu
    assert client.get("/api/monatsabschluss/2026-09").json()["mail"] == neu
    r = client.put("/api/monatsabschluss/mail", json={**neu, "an": "keine adresse"})
    assert r.status_code == 400 and "E-Mail-Adresse" in r.json()["detail"]


def test_stand_ohne_paperless(client, db):
    db.beleg_anlegen(Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND, rechnungsdatum=date(2026, 9, 3),
                           netto=D("10"), steuer=D("1.90"), partner="Strato", pruefen=True))
    umsaetze = [(date(2026, 9, 4), "-11.90", "Strato", 1), (date(2026, 9, 28), "-3.80", "Kiosk", None),
                (date(2026, 10, 1), "-5", "Oktober", None)]
    for tag, betrag, partner, beleg in umsaetze:
        db.umsatz_anlegen(Umsatz(buchungsdatum=tag, betrag=D(betrag), partner=partner, beleg_id=beleg,
                                 pruefsumme=partner))
    db.stundenzettel_monat_setzen("2026-09", [(date(2026, 9, 2), D(10)), (date(2026, 9, 9), D(10))])

    s = client.get("/api/monatsabschluss/2026-09").json()
    assert (s["monat"], s["umsaetze"], s["letzte_buchung"], s["ohne_beleg"]) == ("September 2026", 2, "2026-09-28", 1)
    assert (s["belege"], s["pruefen"], s["fehlerhaft"]) == (1, 1, 0)
    assert (s["paperless"], s["kontoauszug"], s["protokoll"]) == (False, None, None)
    assert s["stundenzettel"] == "20"
    assert client.get("/api/monatsabschluss/2026-13").status_code == 400


def test_stand_mit_paperless(db, umrechner):
    pl = PaperlessClient("http://paperless.local", "t",
                         httpx.Client(transport=httpx.MockTransport(paperless_mit_dokumenten)))
    s = monatsabschluss.stand(db, berechnen([], 2026, 9, umrechner), [], pl, PAPERLESS)
    assert (s["kontoauszug"], s["protokoll"], s["paperless_fehler"]) == (True, True, "")
    s = monatsabschluss.stand(db, berechnen([], 2026, 8, umrechner), [], pl, PAPERLESS)
    assert (s["kontoauszug"], s["protokoll"]) == (False, True)


def test_stand_paperless_nicht_erreichbar(db, umrechner):
    def kaputt(request):
        raise httpx.ConnectError("nicht erreichbar")

    pl = PaperlessClient("http://paperless.local", "t", httpx.Client(transport=httpx.MockTransport(kaputt)))
    s = monatsabschluss.stand(db, berechnen([], 2026, 9, umrechner), [], pl, PAPERLESS)
    assert s["kontoauszug"] is None and "nicht erreichbar" in s["paperless_fehler"]


def _ablage(protokoll: dict, doppelt: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        if request.method == "GET" and pfad == "/api/tags/":
            return httpx.Response(200, json={"next": None, "results": [{"id": 2, "name": "Kontoauszug"}]})
        if request.method == "GET" and pfad == "/api/correspondents/":
            return httpx.Response(200, json={"next": None, "results": []})
        if request.method == "POST" and pfad == "/api/tags/":
            protokoll.setdefault("neue_tags", []).append(json.loads(request.content)["name"])
            return httpx.Response(201, json={"id": 9, "name": "neu"})
        if pfad == "/api/documents/post_document/":
            protokoll["upload"] = request.content.decode("latin-1")
            return httpx.Response(200, json="task-1")
        if pfad == "/api/tasks/":
            protokoll["abfragen"] = protokoll.get("abfragen", 0) + 1
            if protokoll["abfragen"] == 1:
                return httpx.Response(200, json=[{"task_id": "task-1", "status": "STARTED"}])
            if doppelt:
                return httpx.Response(200, json=[{"task_id": "task-1", "status": "FAILURE",
                                                  "result": "Not consuming x.pdf: It is a duplicate of x (#77)."}])
            return httpx.Response(200, json=[{"task_id": "task-1", "status": "SUCCESS", "related_document": "123"}])
        if pfad == "/api/documents/bulk_edit/":
            protokoll["bulk"] = json.loads(request.content)
            return httpx.Response(200, json={"result": "OK"})
        raise AssertionError(f"unerwartet: {request.method} {pfad}")

    return PaperlessClient("http://paperless.local", "t", httpx.Client(transport=httpx.MockTransport(handler)))


def test_kontoauszug_ablegen():
    p: dict = {}
    e = monatsabschluss.ablegen(_ablage(p), PAPERLESS, "kontoauszug", 2026, 9, "statement.pdf", PDF,
                                date(2026, 10, 11), pause=0)
    assert e == {"zustand": "fertig", "dokument": "123", "fehler": "", "titel": "Kontoauszug September 2026",
                 "tag": "Kontoauszug"}
    assert "Kontoauszug September 2026" in p["upload"] and "2026-09-30" in p["upload"]
    assert 'name="tags"\r\n\r\n2\r\n' in p["upload"] and "neue_tags" not in p


def test_protokoll_ablegen_legt_tag_an_und_ergaenzt_tag_bei_doppel():
    p: dict = {}
    e = monatsabschluss.ablegen(_ablage(p, doppelt=True), PAPERLESS, "protokoll", 2026, 9, "", PDF,
                                date(2026, 10, 9), pause=0)
    assert (e["zustand"], e["dokument"], e["titel"]) == ("doppelt", "77", "ELSTER-Protokoll UStVA September 2026")
    assert p["neue_tags"] == ["UStVA-Protokoll"]
    assert "2026-10-09" in p["upload"]
    assert p["bulk"] == {"documents": [77], "method": "add_tag", "parameters": {"tag": 9}}


def test_ablegen_nur_pdf():
    with pytest.raises(ValueError, match="PDF"):
        monatsabschluss.ablegen(_ablage({}), PAPERLESS, "kontoauszug", 2026, 9, "x.csv", b"a;b", date(2026, 10, 1))
    with pytest.raises(ValueError, match="Unbekannte"):
        monatsabschluss.ablegen(_ablage({}), PAPERLESS, "rechnung", 2026, 9, "x.pdf", PDF, date(2026, 10, 1))


def test_ablegen_ohne_paperless(client):
    r = client.post("/api/monatsabschluss/2026-09/ablegen/kontoauszug", content=PDF)
    assert r.status_code == 400 and "Paperless" in r.json()["detail"]
