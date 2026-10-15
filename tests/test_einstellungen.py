import secrets
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from ustva import einstellungen
from ustva.__main__ import main
from ustva.config import Config
from ustva.db import Datenbank
from ustva.web import create_app

# Testpasswörter zur Laufzeit erzeugen, damit keine festen Zugangsdaten im Code stehen.
PW_ENV, PW_NEU, PW_ERST, PW_CLI = (secrets.token_urlsafe(12) for _ in range(4))


def _mit_zugang(benutzer: str, kennwort: str) -> Config:
    return Config(app_benutzer=benutzer, app_passwort=kennwort)


def _angemeldet(app, benutzer: str, passwort: str) -> TestClient:
    """Eigener Browser (eigene Cookies), angemeldet; prüft, dass die Anmeldung klappt."""
    c = TestClient(app)
    r = c.post("/api/anmelden", json={"benutzer": benutzer, "passwort": passwort})
    assert r.status_code == 200, r.text
    return c


def _anmelden_klappt(app, benutzer: str, passwort: str) -> bool:
    return TestClient(app).post("/api/anmelden", json={"benutzer": benutzer, "passwort": passwort}).status_code == 200


@pytest.fixture(autouse=True)
def schneller_hash(monkeypatch):
    monkeypatch.setattr(einstellungen, "ITERATIONEN", 1000)
    monkeypatch.setattr(einstellungen.passwort_hash, "__defaults__", (None, 1000))


def test_hash():
    h = einstellungen.passwort_hash(PW_NEU)
    assert h.startswith("pbkdf2_sha256$1000$") and PW_NEU not in h
    assert einstellungen.passwort_stimmt(PW_NEU, h)
    assert not einstellungen.passwort_stimmt("falsch", h)
    assert not einstellungen.passwort_stimmt("x", "kaputt")


def test_paperless_einstellungen_ueberschreiben_umgebung(db, umrechner):
    cfg = Config(paperless_url="http://alt.local", paperless_token="aus-env")
    c = TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 10)))

    e = c.get("/api/einstellungen").json()
    assert e["paperless_url"] == {"wert": "http://alt.local", "quelle": "umgebung"}
    assert e["paperless_token"] == {"gesetzt": True, "quelle": "umgebung"}

    e = c.put("/api/einstellungen", json={"paperless_url": "https://paperless.example/ ",
                                          "paperless_token": "neues-token", "bundesland": "nw",
                                          "eigene_ustid": "de 123456789"}).json()
    assert e["paperless_url"] == {"wert": "https://paperless.example", "quelle": "app"}
    assert e["paperless_token"]["quelle"] == "app"
    assert "neues-token" not in str(e)
    assert (e["bundesland"]["wert"], e["eigene_ustid"]["wert"]) == ("NW", "DE123456789")
    status = c.get("/api/status").json()
    assert (status["paperless"], status["eigene_ustid"]) == ("https://paperless.example", "DE123456789")
    assert einstellungen.wirksam(cfg, db).paperless_token == "neues-token"

    # leerer Wert: wieder die Umgebung
    e = c.put("/api/einstellungen", json={"paperless_token": ""}).json()
    assert e["paperless_token"]["quelle"] == "umgebung"
    assert einstellungen.wirksam(cfg, db).paperless_token == "aus-env"

    r = c.put("/api/einstellungen", json={"paperless_url": "paperless.local"})
    assert r.status_code == 400 and "http://" in r.json()["detail"]
    assert c.put("/api/einstellungen", json={"bundesland": "XX"}).status_code == 400


def test_paperless_test(db, umrechner, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] != "Token richtig":
            return httpx.Response(401)
        return httpx.Response(200, json={"next": None, "results": [{"id": 1, "name": "Eingangsrechnung"}]})

    echt = httpx.Client
    monkeypatch.setattr("ustva.web.httpx.Client", lambda **kw: echt(transport=httpx.MockTransport(handler)))
    c = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))
    pfad = "/api/einstellungen/paperless-test"
    r = c.post(pfad, json={"paperless_url": "http://p.local", "paperless_token": "richtig"})
    assert r.json() == {"text": "Verbindung klappt, 1 Tags gefunden."}
    r = c.post(pfad, json={"paperless_url": "http://p.local", "paperless_token": "x"})
    assert r.status_code == 400 and "Token" in r.json()["detail"]


def test_passwort_aendern_mit_umgebungs_zugang(db, umrechner):
    cfg = _mit_zugang("admin", PW_ENV)
    app = create_app(cfg, db, umrechner, heute=date(2026, 10, 10))
    assert TestClient(app).get("/api/status").status_code == 401
    c = _angemeldet(app, "admin", PW_ENV)
    anderer = _angemeldet(app, "admin", PW_ENV)
    assert c.get("/api/status").json()["anmeldung"] is True

    r = c.post("/api/passwort", json={"aktuell": "falsch", "neu": PW_NEU})
    assert r.status_code == 403
    r = c.post("/api/passwort", json={"aktuell": PW_ENV, "neu": "kurz"})
    assert r.status_code == 400
    r = c.post("/api/passwort", json={"aktuell": PW_ENV, "neu": PW_NEU})
    assert r.json()["quelle"] == "app"

    # dieser Browser bleibt angemeldet, alle anderen Sitzungen sind beendet
    assert c.get("/api/status").status_code == 200
    assert anderer.get("/api/status").status_code == 401
    assert not _anmelden_klappt(app, "admin", PW_ENV)
    assert _anmelden_klappt(app, "admin", PW_NEU)
    assert PW_NEU not in str(db.einstellungen())


def test_passwort_ohne_bisherige_anmeldung(db, umrechner):
    app = create_app(Config(), db, umrechner, heute=date(2026, 10, 10))
    c = TestClient(app)
    assert c.get("/api/status").json()["anmeldung"] is False
    assert c.get("/api/anmeldung").json() == {"aktiv": False, "angemeldet": True}
    c.post("/api/passwort", json={"benutzer": "marcel", "neu": PW_ERST})
    # wer das erste Passwort setzt, bleibt angemeldet; ein anderer Browser nicht
    assert c.get("/api/status").status_code == 200
    assert TestClient(app).get("/api/status").status_code == 401
    assert _anmelden_klappt(app, "marcel", PW_ERST)


def test_kommandozeile_setzt_und_entfernt_passwort(tmp_path, monkeypatch, umrechner):
    pfad = tmp_path / "db.sqlite"
    monkeypatch.setenv("DATENBANK", str(pfad))
    monkeypatch.setenv("APP_BENUTZER", "admin")
    monkeypatch.setenv("APP_PASSWORT", PW_ENV)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PW_CLI)
    db = Datenbank(pfad)
    app = create_app(Config.aus_umgebung(), db, umrechner, heute=date(2026, 10, 10))
    c = _angemeldet(app, "admin", PW_ENV)
    assert c.get("/api/status").status_code == 200

    assert main(["passwort"]) == 0
    # der laufende Dienst merkt die Änderung ohne Neustart, bestehende Sitzungen enden
    assert c.get("/api/status").status_code == 401
    assert not _anmelden_klappt(app, "admin", PW_ENV)
    c = _angemeldet(app, "admin", PW_CLI)

    assert main(["passwort", "--zuruecksetzen"]) == 0
    assert c.get("/api/status").status_code == 401
    assert _anmelden_klappt(app, "admin", PW_ENV)
