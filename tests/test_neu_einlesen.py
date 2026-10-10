from datetime import date
from decimal import Decimal as D

import httpx
from fastapi.testclient import TestClient

from ustva.config import Config
from ustva.models import Beleg
from ustva.quellen import neu_einlesen, verzeichnis
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app

from . import beispiele


def _alt_falsch(db, b: Beleg) -> Beleg:
    """Simuliert einen Beleg, den eine ältere Version falsch erkannt hat."""
    b.steuer, b.hinweise = D("-4.19"), []
    return db.beleg_aktualisieren(b)


def _verzeichnis(tmp_path, db):
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / "hetzner.txt").write_text(beispiele.HETZNER["zeilen"], encoding="utf-8")
    cfg = Config(rechnungs_verzeichnis=tmp_path, eigene_ustid="DE123456789")
    assert verzeichnis.importieren(db, cfg).neu == 1
    return cfg


def test_verzeichnis_beleg_wird_neu_gelesen(tmp_path, db):
    cfg = _verzeichnis(tmp_path, db)
    alt = _alt_falsch(db, db.alle_belege()[0])
    alt.zahlungsdatum = date(2026, 9, 25)  # z. B. aus dem Kontoabgleich
    db.beleg_aktualisieren(alt)
    manuell = db.beleg_anlegen(Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND,
                                     rechnungsdatum=date(2026, 9, 1), netto=D("5.00"), steuer=D("-1.00")))

    ergebnis = neu_einlesen.neu_einlesen(db, cfg)
    assert (ergebnis.aktualisiert, ergebnis.neu, ergebnis.fehler) == (1, 0, [])

    neu = db.beleg(alt.id)
    assert (neu.netto, neu.steuer) == (D("14.19"), D("2.70"))
    assert neu.zahlungsdatum == date(2026, 9, 25)
    assert neu.pruefen
    assert db.beleg(manuell.id).steuer == D("-1.00")  # manuelle Belege bleiben unberührt
    assert len(db.alle_belege()) == 2


def test_geprueft_und_geaendert_bleiben_stehen(tmp_path, db):
    cfg = _verzeichnis(tmp_path, db)
    b = _alt_falsch(db, db.alle_belege()[0])

    (tmp_path / "eingang" / "hetzner.txt").write_text(beispiele.HETZNER["zeilen"] + "\nNachtrag", encoding="utf-8")
    ergebnis = neu_einlesen.neu_einlesen(db, cfg, [b.id])
    assert ergebnis.aktualisiert == 0
    assert "geändert" in ergebnis.fehler[0]

    b.pruefen = False
    db.beleg_aktualisieren(b)
    ergebnis = neu_einlesen.neu_einlesen(db, cfg, [b.id])
    assert ergebnis.aktualisiert == 0
    assert "geprüft" in ergebnis.fehler[0]
    assert db.beleg(b.id).steuer == D("-4.19")


def test_paperless_beleg_wird_neu_gelesen(db):
    dok = {"id": 21, "title": "Hetzner Rechnung", "content": beispiele.HETZNER["zellen"], "created": "2026-09-21",
           "correspondent": 3, "document_type": 1, "tags": [1], "custom_fields": []}
    listen = {"/api/correspondents/": [{"id": 3, "name": "Hetzner"}], "/api/custom_fields/": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/documents/21/":
            return httpx.Response(200, json=dok)
        return httpx.Response(200, json={"count": 1, "next": None, "results": listen[request.url.path]})

    alt = db.beleg_anlegen(Beleg(art=Art.EINGANG, steuerfall=Steuerfall.VORSTEUER_INLAND,
                                 rechnungsdatum=date(2026, 9, 21), netto=D("14.19"), steuer=D("-4.19"),
                                 quelle="paperless", quelle_ref="21", pruefen=True))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="geheim", eigene_ustid="DE123456789")
    ergebnis = neu_einlesen.neu_einlesen(db, cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert (ergebnis.aktualisiert, ergebnis.fehler) == (1, [])

    neu = db.beleg(alt.id)
    assert (neu.netto, neu.steuer, neu.partner) == (D("14.19"), D("2.70"), "Hetzner")
    assert neu.quelle_link == "http://paperless.local/documents/21/details"


def test_endpunkt(tmp_path, db, umrechner):
    cfg = _verzeichnis(tmp_path, db)
    b = _alt_falsch(db, db.alle_belege()[0])
    client = TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 10)))
    r = client.post("/api/belege/neu-einlesen", json={})
    assert r.json() == {"aktualisiert": 1, "neu": 0, "fehler": []}
    assert db.beleg(b.id).steuer == D("2.70")
