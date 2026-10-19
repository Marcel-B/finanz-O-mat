"""Anmeldung an der Weboberfläche: Sitzungen, Ablauf, Sperre nach Fehlversuchen."""

import secrets
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ustva import einstellungen
from ustva.config import Config
from ustva.web import create_app

# Testpasswort zur Laufzeit erzeugen, damit keine festen Zugangsdaten im Code stehen.
PW = secrets.token_urlsafe(12)


def _mit_zugang(kennwort: str) -> Config:
    return Config(app_benutzer="marcel", app_passwort=kennwort)


class Uhr:
    def __init__(self):
        self.jetzt = 1_000_000.0

    def __call__(self) -> float:
        return self.jetzt


@pytest.fixture(autouse=True)
def schneller_hash(monkeypatch):
    monkeypatch.setattr(einstellungen, "ITERATIONEN", 1000)
    monkeypatch.setattr(einstellungen.passwort_hash, "__defaults__", (None, 1000))


@pytest.fixture
def uhr(monkeypatch):
    u = Uhr()
    monkeypatch.setattr(einstellungen, "_jetzt", u)
    monkeypatch.setattr(einstellungen, "_monoton", u)
    return u


@pytest.fixture
def app(db, umrechner, uhr):
    cfg = _mit_zugang(PW)
    return create_app(cfg, db, umrechner, heute=date(2026, 10, 10))


def _anmelden(c: TestClient, passwort: str = PW, benutzer: str = "marcel"):
    return c.post("/api/anmelden", json={"benutzer": benutzer, "passwort": passwort})


def test_ohne_anmeldung_nur_seite_und_anmeldung(app):
    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/docs").status_code == 401
    assert c.get("/api/anmeldung").json() == {"aktiv": True, "art": einstellungen.ART_PASSWORT,
                                                  "angemeldet": False}
    # Basic Auth gilt nicht mehr, der Browser soll auch nicht danach fragen
    r = c.get("/api/status", auth=("marcel", PW))
    assert r.status_code == 401 and "www-authenticate" not in r.headers


def test_anmelden_und_abmelden(app, db):
    c = TestClient(app)
    r = _anmelden(c)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "secure" not in cookie  # http:// im Heimnetz
    wert = c.cookies[einstellungen.COOKIE]
    assert wert not in str(db.sitzung(einstellungen._schluessel(wert)))
    assert c.get("/api/status").status_code == 200
    assert c.get("/api/anmeldung").json()["angemeldet"] is True

    assert c.post("/api/abmelden").status_code == 204
    assert c.get("/api/status").status_code == 401
    # auch mit dem alten Cookie geht nichts mehr
    c.cookies.set(einstellungen.COOKIE, wert)
    assert c.get("/api/status").status_code == 401


def test_cookie_ueber_https_nur_sicher(app):
    c = TestClient(app, base_url="https://finanz-o-mat.example.ts.net")
    assert "secure" in _anmelden(c).headers["set-cookie"].lower()
    c = TestClient(app)
    r = c.post("/api/anmelden", json={"benutzer": "marcel", "passwort": PW}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_falsche_anmeldung(app):
    c = TestClient(app)
    for benutzer, passwort in (("marcel", "falsch"), ("admin", PW), ("", "")):
        r = _anmelden(c, passwort, benutzer)
        assert r.status_code == 401 and r.json()["detail"] == "Benutzername oder Passwort stimmt nicht."
    assert einstellungen.COOKIE not in c.cookies


def test_sitzung_laeuft_ab(app, uhr):
    c = TestClient(app)
    _anmelden(c)
    uhr.jetzt += einstellungen.SITZUNG_LEERLAUF - 60
    assert c.get("/api/status").status_code == 200  # Nutzung verlängert
    uhr.jetzt += einstellungen.SITZUNG_LEERLAUF - 60
    assert c.get("/api/status").status_code == 200
    uhr.jetzt += einstellungen.SITZUNG_LEERLAUF + 1
    assert c.get("/api/status").status_code == 401

    # auch bei ständiger Nutzung ist nach SITZUNG_HOECHSTENS Schluss
    _anmelden(c)
    for _ in range(einstellungen.SITZUNG_HOECHSTENS // 3600):
        uhr.jetzt += 3600
        assert c.get("/api/status").status_code == 200
    uhr.jetzt += 3600
    assert c.get("/api/status").status_code == 401


def test_sperre_nach_fehlversuchen(app, uhr):
    c = TestClient(app)
    for _ in range(einstellungen.FEHLVERSUCHE - 1):
        assert _anmelden(c, "falsch").status_code == 401
    r = _anmelden(c, "falsch")
    assert r.status_code == 429 and r.headers["retry-after"] == str(einstellungen.SPERRDAUER)
    assert "15 Minuten" in r.json()["detail"]
    # gesperrt: auch das richtige Passwort wird nicht geprüft
    assert _anmelden(c).status_code == 429

    uhr.jetzt += einstellungen.SPERRDAUER
    assert _anmelden(c).status_code == 200


def test_fehlversuche_verfallen(app, uhr):
    c = TestClient(app)
    for _ in range(einstellungen.FEHLVERSUCHE - 1):
        _anmelden(c, "falsch")
    uhr.jetzt += einstellungen.SPERRFENSTER
    assert _anmelden(c, "falsch").status_code == 401


def test_sperre_fuer_alle_adressen():
    sperre = einstellungen.Sperre(uhr=lambda: 0.0)
    for i in range(einstellungen.FEHLVERSUCHE_GESAMT - 1):
        assert sperre.fehlschlag(f"10.0.0.{i}") == 0
    assert sperre.fehlschlag("10.0.1.1") == einstellungen.SPERRDAUER
    assert sperre.wartezeit("192.168.1.2") == einstellungen.SPERRDAUER


def test_passwort_aendern_ist_gebremst(app):
    c = TestClient(app)
    _anmelden(c)
    for _ in range(einstellungen.FEHLVERSUCHE - 1):
        assert c.post("/api/passwort", json={"aktuell": "falsch", "neu": PW + "x"}).status_code == 403
    assert c.post("/api/passwort", json={"aktuell": "falsch", "neu": PW + "x"}).status_code == 429


def test_neues_umgebungs_passwort_beendet_sitzungen(db, umrechner, uhr):
    c = TestClient(create_app(_mit_zugang(PW), db, umrechner))
    _anmelden(c)
    assert c.get("/api/status").status_code == 200
    # Dienst mit anderem APP_PASSWORT neu gestartet, gleiche Datenbank
    neu = TestClient(create_app(_mit_zugang(PW + "2"), db, umrechner))
    neu.cookies.update(c.cookies)
    assert neu.get("/api/status").status_code == 401
    # unverändert neu gestartet: Sitzung bleibt
    gleich = TestClient(create_app(_mit_zugang(PW), db, umrechner))
    gleich.cookies.update(c.cookies)
    assert gleich.get("/api/status").status_code == 200
    assert not any(PW in str(w) for w in db.einstellungen().values())
