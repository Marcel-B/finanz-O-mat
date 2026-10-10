import json
from datetime import date

from fastapi.testclient import TestClient

from ustva.config import Config
from ustva.web import create_app


def _client(db, umrechner, verzeichnis=None):
    return TestClient(create_app(Config(backup_verzeichnis=verzeichnis), db, umrechner, heute=date(2026, 10, 10)))


def test_ohne_backup_verzeichnis_aus(db, umrechner):
    client = _client(db, umrechner)
    assert client.get("/api/backup").json() == {"unterstuetzt": False}
    assert client.post("/api/backup").status_code == 409


def test_nicht_eingerichtet(db, umrechner, tmp_path):
    client = _client(db, umrechner, tmp_path)
    assert client.get("/api/backup").json()["zustand"] == "aus"
    assert client.post("/api/backup").status_code == 409
    assert not (tmp_path / "anfordern").exists()


def test_stand_und_jetzt_sichern(db, umrechner, tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({
        "zustand": "ok", "ziel": "nx1.your-storageshare.de / Backups/finanz-o-mat",
        "letzte_sicherung": "2026-10-10T02:41:07+02:00", "anzahl": 3, "groesse": 245760,
    }))
    client = _client(db, umrechner, tmp_path)
    status = client.get("/api/backup").json()
    assert status["anzahl"] == 3
    assert status["letzte_sicherung"] == "2026-10-10T02:41:07+02:00"

    r = client.post("/api/backup")
    assert r.status_code == 202
    assert r.json()["zustand"] == "angefordert"
    assert (tmp_path / "anfordern").exists()


def test_laufendes_backup_nicht_doppelt(db, umrechner, tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({"zustand": "laeuft"}))
    client = _client(db, umrechner, tmp_path)
    assert client.post("/api/backup").json()["zustand"] == "laeuft"
    assert not (tmp_path / "anfordern").exists()
