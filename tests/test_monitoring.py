"""Messwerte unter /metrics und Versand der Protokollzeilen an Loki."""

import json
import logging
import logging.config
import secrets
from datetime import UTC, date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families

from ustva import einstellungen, monitoring
from ustva.config import Config
from ustva.web import create_app

BELEG = {"art": "eingang", "steuerfall": "inland", "rechnungsdatum": "2026-09-15", "netto": "100.00",
         "steuersatz": "19", "partner": "Muster GmbH", "rechnungsnummer": "RE-12345"}


@pytest.fixture
def client(db, umrechner, tmp_path):
    (tmp_path / "eingang").mkdir()
    (tmp_path / "eingang" / "rechnung.txt").write_text("Rechnungsdatum 05.09.2026\nSumme netto 10,00\nMwSt 19 % 1,90")
    return TestClient(create_app(Config(rechnungs_verzeichnis=tmp_path), db, umrechner, heute=date(2026, 10, 10)))


def _wert(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_ohne_anmeldung_und_ohne_inhalte(db, umrechner, monkeypatch):
    monkeypatch.setattr(einstellungen, "ITERATIONEN", 1000)
    pw = secrets.token_urlsafe(12)
    cfg = Config(app_benutzer="admin", app_passwort=pw)
    c = TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 10)))
    c.post("/api/anmelden", json={"benutzer": "admin", "passwort": pw})
    assert c.post("/api/belege", json=BELEG).status_code == 201

    text = TestClient(c.app).get("/metrics").text  # ohne Sitzungs-Cookie
    assert 'finanzomat_belege{art="eingang"} 1.0' in text
    assert "finanzomat_umsaetze_ohne_beleg 0.0" in text
    assert "finanzomat_http_requests_total" in text
    # Weder Namen noch Nummern oder Beträge
    for geheim in ("Muster", "RE-12345", "100.00", pw):
        assert geheim not in text


def test_anfragen_mit_routenvorlage(client):
    vorher = _wert("finanzomat_http_requests_total", method="GET", route="/api/belege/{beleg_id}", status="404")
    assert client.get("/api/belege/987").status_code == 404
    nachher = _wert("finanzomat_http_requests_total", method="GET", route="/api/belege/{beleg_id}", status="404")
    assert nachher == vorher + 1
    assert "/api/belege/987" not in client.get("/metrics").text


def test_import_wird_gezaehlt(client):
    vorher = _wert("finanzomat_import_belege_total", quelle="verzeichnis", ergebnis="neu")
    client.post("/api/import", json={"quelle": "verzeichnis", "zeitraum": "2026-09"})
    assert _wert("finanzomat_import_belege_total", quelle="verzeichnis", ergebnis="neu") == vorher + 1


def test_backup_und_update_aus_statusdateien(db, umrechner, tmp_path):
    (tmp_path / "backup").mkdir()
    (tmp_path / "backup" / "status.json").write_text(json.dumps(
        {"zustand": "ok", "letzte_sicherung": "2026-10-10T02:30:05+02:00"}))
    (tmp_path / "update").mkdir()
    (tmp_path / "update" / "status.json").write_text(json.dumps({"zustand": "neu", "neu": "abc1234"}))
    cfg = Config(backup_verzeichnis=tmp_path / "backup", update_verzeichnis=tmp_path / "update")
    text = TestClient(create_app(cfg, db, umrechner)).get("/metrics").text
    assert "finanzomat_backup_ok 1.0" in text
    werte = {s.name: s.value for f in text_string_to_metric_families(text) for s in f.samples}
    assert werte["finanzomat_backup_last_success_timestamp_seconds"] == datetime(2026, 10, 10, 0, 30, 5,
                                                                                  tzinfo=UTC).timestamp()
    assert "finanzomat_update_available 1.0" in text


def test_quelle_und_stufe():
    assert monitoring.quelle("ustva.quellen.paperless") == "paperless"
    assert monitoring.quelle("ustva.web") == "web"
    assert monitoring.quelle("uvicorn.error") == "server"
    assert monitoring.quelle("uvicorn.access") == "zugriff"
    record = logging.LogRecord("x", logging.CRITICAL, "", 0, "", None, None)
    assert monitoring.stufe(record) == "error"


def _loki(antworten: list):
    gesendet = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesendet.append(json.loads(request.content))
        return antworten.pop(0) if antworten else httpx.Response(204)

    return gesendet, httpx.Client(transport=httpx.MockTransport(handler))


def test_loki_handler_schickt_zeilen_mit_labels():
    gesendet, client = _loki([])
    h = monitoring.LokiHandler("http://monitoring:3100", "proxmox", intervall=3600, client=client)
    assert h.url == "http://monitoring:3100/loki/api/v1/push"
    log = logging.getLogger("ustva.quellen.paperless")
    log.addHandler(h)
    try:
        log.warning("Paperless antwortet nicht")
        logging.getLogger("httpx").addHandler(h)
        logging.getLogger("httpx").warning("darf nicht zurück an Loki")
        h.senden()
    finally:
        log.removeHandler(h)
        logging.getLogger("httpx").removeHandler(h)
        h.close()
    strom = gesendet[0]["streams"][0]
    assert strom["stream"] == {"app": "finanzomat", "host": "proxmox", "level": "warning", "source": "paperless"}
    assert strom["values"][0][1] == "Paperless antwortet nicht"
    assert len(gesendet) == 1 and len(gesendet[0]["streams"]) == 1


def test_loki_weg_verwirft_ohne_fehler():
    gesendet, client = _loki([httpx.Response(503), httpx.Response(503)])
    warnungen = []
    h = monitoring.LokiHandler("http://monitoring:3100/", "proxmox", intervall=3600, client=client,
                               warnen=warnungen.append)
    vorher = _wert("finanzomat_loki_dropped_total")
    h.handle(logging.LogRecord("ustva.web", logging.ERROR, "", 0, "kaputt", None, None))
    h.handle(logging.LogRecord("ustva.web", logging.ERROR, "", 0, "noch mal", None, None))
    h.senden()
    h.handle(logging.LogRecord("ustva.web", logging.ERROR, "", 0, "und noch mal", None, None))
    h.senden()
    h.close()
    assert _wert("finanzomat_loki_dropped_total") == vorher + 3
    assert len(warnungen) == 1  # nur einmal melden, nicht bei jedem Versuch


def test_loki_schlange_voll_blockiert_nicht():
    _, client = _loki([])
    h = monitoring.LokiHandler("http://monitoring:3100", "proxmox", max_zeilen=1, intervall=3600, client=client)
    vorher = _wert("finanzomat_loki_dropped_total")
    for i in range(3):
        h.handle(logging.LogRecord("ustva.web", logging.INFO, "", 0, f"Zeile {i}", None, None))
    h.close()
    assert _wert("finanzomat_loki_dropped_total") == vorher + 2


def test_log_config_laesst_sich_anwenden():
    root = logging.getLogger()
    alt_handler, alt_level = root.handlers[:], root.level
    try:
        logging.config.dictConfig(monitoring.log_config("info", "http://127.0.0.1:9", "test"))
        namen = {type(h).__name__ for h in root.handlers}
        assert {"StreamHandler", "Zeilenzaehler", "LokiHandler"} <= namen
        assert logging.getLogger("httpx").level == logging.WARNING
        zugriff = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                    ("100.64.0.1:5000", "GET", "/metrics", "1.1", 200), None)
        assert not logging.getLogger("uvicorn.access").filter(zugriff)
    finally:
        for h in root.handlers:
            if h not in alt_handler:
                h.close()
        root.handlers[:] = alt_handler
        root.setLevel(alt_level)
    assert "loki" not in monitoring.log_config("INFO")["handlers"]
