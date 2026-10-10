import json
from datetime import date

from fastapi.testclient import TestClient

from ustva.config import Config
from ustva.web import create_app


def _client(db, umrechner, verzeichnis=None):
    return TestClient(create_app(Config(update_verzeichnis=verzeichnis), db, umrechner, heute=date(2026, 10, 10)))


def test_ohne_update_verzeichnis_aus(db, umrechner):
    client = _client(db, umrechner)
    assert client.get("/api/update").json() == {"unterstuetzt": False}
    assert client.post("/api/update").status_code == 409


def test_neue_version_melden_und_anfordern(db, umrechner, tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({
        "zustand": "neu", "installiert": "abc1234", "neu": "def5678",
        "aenderungen": ["Vue-Oberfläche", "N26-Import"], "geprueft": "2026-10-10T12:00:00+02:00",
    }))
    client = _client(db, umrechner, tmp_path)
    status = client.get("/api/update").json()
    assert status["zustand"] == "neu"
    assert status["aenderungen"] == ["Vue-Oberfläche", "N26-Import"]

    r = client.post("/api/update")
    assert r.status_code == 202
    assert r.json()["zustand"] == "angefordert"
    assert (tmp_path / "anfordern").exists()


def test_laufendes_update_nicht_doppelt_anfordern(db, umrechner, tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({"zustand": "laeuft"}))
    client = _client(db, umrechner, tmp_path)
    assert client.post("/api/update").json()["zustand"] == "laeuft"
    assert not (tmp_path / "anfordern").exists()


def test_kaputte_statusdatei(db, umrechner, tmp_path):
    (tmp_path / "status.json").write_text("{kein json")
    assert _client(db, umrechner, tmp_path).get("/api/update").json()["zustand"] == "unbekannt"
