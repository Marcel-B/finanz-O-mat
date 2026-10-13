"""Messwerte für Prometheus (``GET /metrics``) und Protokoll an Loki.

Der gemeinsame Monitoring-Stack (Grafana, Prometheus, Loki) ist im Repo Marcel-B/scripts unter
docs/monitoring.md beschrieben. Konventionen von dort: Namen beginnen mit ``finanzomat_``, Labels haben nur
wenige feste Werte, und weil ``/metrics`` keinen Login hat, stehen dort nur Zahlen, keine Beträge, Namen
oder Rechnungsnummern.

Logs gehen an ``<LOKI_URL>/loki/api/v1/push`` mit den Labels ``app``, ``host``, ``level`` und ``source``.
Ein eigener Thread schickt sie gesammelt; ist Loki nicht erreichbar, verwirft er die Zeilen, statt die App
aufzuhalten. Das Protokoll im Journal des Containers bleibt davon unberührt.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from starlette.routing import Match

from . import aktualisierung, backup

# Keine zusätzlichen *_created-Reihen je Zähler
disable_created_metrics()

APP = "finanzomat"
LOKI_PFAD = "/loki/api/v1/push"

ANFRAGEN = Counter("finanzomat_http_requests_total", "HTTP-Anfragen", ["method", "route", "status"])
DAUER = Histogram("finanzomat_http_request_duration_seconds", "Dauer der HTTP-Anfragen", ["route"],
                  buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
IMPORTE = Counter("finanzomat_import_belege_total", "Eingelesene Belege und Kontoumsätze je Quelle",
                  ["quelle", "ergebnis"])
ANMELDUNGEN = Counter("finanzomat_anmeldungen_total", "Anmeldeversuche an der Weboberfläche", ["ergebnis"])
LOGZEILEN = Counter("finanzomat_log_lines_total", "Protokollzeilen je Quelle und Stufe", ["source", "level"])
LOKI_VERWORFEN = Counter("finanzomat_loki_dropped_total", "Protokollzeilen, die Loki nicht erreicht haben")

STUFEN = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warning"}
# Diese Logger schreibt der Versender selbst; sie dürfen nicht wieder bei ihm landen
EIGENE_LOGGER = ("httpx", "httpcore")


def stufe(record: logging.LogRecord) -> str:
    if record.levelno >= logging.ERROR:
        return "error"
    return STUFEN.get(record.levelno, "debug" if record.levelno < logging.INFO else "info")


def quelle(name: str) -> str:
    """Wenige feste Werte für das Label source, abgeleitet vom Namen des Loggers."""
    if name == "uvicorn.access":
        return "zugriff"
    if name.split(".")[0] == "uvicorn":
        return "server"
    teile = name.split(".")
    if teile[0] == "ustva":
        return teile[-1] if len(teile) > 1 else "app"
    return teile[0] or "app"


# --- Messwerte -----------------------------------------------------------------------------------------------

def import_zaehlen(name: str, ergebnis: dict) -> None:
    """Zählt das Ergebnis eines Imports (ImportErgebnis.to_dict() oder BankImport.to_dict())."""
    for schluessel in ("neu", "vorhanden", "uebersprungen", "aktualisiert", "zugeordnet"):
        if wert := ergebnis.get(schluessel):
            IMPORTE.labels(name, schluessel).inc(wert)
    if fehler := ergebnis.get("fehler"):
        IMPORTE.labels(name, "fehler").inc(len(fehler) if isinstance(fehler, list) else fehler)


def _route(request) -> str:
    """Pfadvorlage wie /api/belege/{beleg_id}, damit IDs nicht zu eigenen Messreihen werden."""
    for r in request.app.router.routes:
        pfad = getattr(r, "path", "")
        # Die Oberfläche hängt als Mount an / und passt auf jeden Pfad, daher nur echte Routen nehmen
        if (pfad.startswith("/api") or pfad == "/metrics") and r.matches(request.scope)[0] == Match.FULL:
            return pfad
    return "unbekannt" if request.url.path.startswith("/api") else "oberflaeche"


async def messen(request, call_next):
    """Middleware: zählt Anfragen und misst ihre Dauer."""
    start = time.perf_counter()
    status = 500
    try:
        antwort = await call_next(request)
        status = antwort.status_code
        return antwort
    finally:
        route = _route(request)
        ANFRAGEN.labels(request.method, route, str(status)).inc()
        DAUER.labels(route).observe(time.perf_counter() - start)


class Bestand:
    """Werte, die beim Abruf aus Datenbank und Statusdateien gelesen werden (nur Anzahlen, keine Beträge)."""

    def __init__(self, db, update_verzeichnis: Path | None, backup_verzeichnis: Path | None):
        self.db = db
        self.update_verzeichnis = update_verzeichnis
        self.backup_verzeichnis = backup_verzeichnis

    def collect(self):
        belege = self.db.alle_belege()
        anzahl = GaugeMetricFamily("finanzomat_belege", "Erfasste Belege je Art", labels=["art"])
        for art in ("ausgang", "eingang"):
            anzahl.add_metric([art], sum(1 for b in belege if b.art == art))
        yield anzahl
        yield GaugeMetricFamily("finanzomat_belege_zu_pruefen", "Belege mit Prüfhinweis",
                                value=sum(1 for b in belege if b.pruefen))
        umsaetze = self.db.alle_umsaetze()
        yield GaugeMetricFamily("finanzomat_umsaetze_ohne_beleg", "Kontoumsätze ohne Beleg (nicht ignoriert)",
                                value=sum(1 for u in umsaetze if u.beleg_id is None and not u.ignoriert))

        b = backup.lesen(self.backup_verzeichnis)
        if b["unterstuetzt"]:
            ok = GaugeMetricFamily("finanzomat_backup_ok", "1, wenn das letzte Nextcloud-Backup geklappt hat")
            ok.add_metric([], 1 if b["zustand"] in ("ok", "laeuft", "angefordert") else 0)
            yield ok
            if zeit := _zeitpunkt(b.get("letzte_sicherung")):
                yield GaugeMetricFamily("finanzomat_backup_last_success_timestamp_seconds",
                                        "Zeitpunkt der letzten Sicherung", value=zeit)
        u = aktualisierung.lesen(self.update_verzeichnis)
        if u["unterstuetzt"]:
            yield GaugeMetricFamily("finanzomat_update_available", "1, wenn eine neue Version bereitsteht",
                                    value=1 if u["zustand"] in ("neu", "angefordert") else 0)


def _zeitpunkt(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def metriken(bestand: Bestand) -> tuple[bytes, str]:
    """Inhalt für GET /metrics: Prozess-, Anfrage- und Ereigniswerte plus der aktuelle Bestand."""
    eigene = CollectorRegistry(auto_describe=False)
    eigene.register(bestand)
    return generate_latest(REGISTRY) + generate_latest(eigene), CONTENT_TYPE_LATEST


# --- Protokoll -----------------------------------------------------------------------------------------------

class Zeilenzaehler(logging.Handler):
    """Zählt Protokollzeilen je Quelle und Stufe, auch ohne Loki (für Warnungen und Fehler im Dashboard)."""

    def emit(self, record: logging.LogRecord) -> None:
        LOGZEILEN.labels(quelle(record.name), stufe(record)).inc()


class OhneMetrics(logging.Filter):
    """Zugriffszeilen für /metrics weglassen: Prometheus fragt alle 30 Sekunden."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3 and str(args[2]).startswith("/metrics"))


def loki_adresse(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.endswith(LOKI_PFAD) else url + LOKI_PFAD


class LokiHandler(logging.Handler):
    """Schickt Protokollzeilen gesammelt an Loki; blockiert nie, verwirft bei Stau oder Fehler."""

    def __init__(self, url: str, host: str, *, max_zeilen: int = 10_000, intervall: float = 2.0,
                 client: httpx.Client | None = None, warnen: Callable[[str], None] | None = None):
        super().__init__()
        self.url = loki_adresse(url)
        self.host = host
        self.intervall = intervall
        self.schlange: queue.Queue = queue.Queue(maxsize=max_zeilen)
        self.client = client or httpx.Client(timeout=5)
        self.warnen = warnen or (lambda text: print(text, file=sys.stderr, flush=True))
        self._zuletzt_gewarnt: float | None = None  # monotonic() kann kurz nach dem Start unter 300 liegen
        self._stopp = threading.Event()
        self._thread = threading.Thread(target=self._laufen, name="loki-versand", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.split(".")[0] in EIGENE_LOGGER or threading.current_thread() is self._thread:
            return
        try:
            zeile = self.format(record)
            labels = (quelle(record.name), stufe(record))
            self.schlange.put_nowait((labels, str(int(record.created * 1e9)), zeile))
        except queue.Full:
            LOKI_VERWORFEN.inc()
        except Exception:  # noqa: BLE001 - Protokollieren darf die App nie stören
            self.handleError(record)

    def _laufen(self) -> None:
        while not self._stopp.is_set():
            self._stopp.wait(self.intervall)
            self.senden()

    def senden(self) -> None:
        """Schickt alles, was in der Schlange liegt (höchstens 1000 Zeilen pro Anfrage)."""
        while True:
            zeilen = []
            while len(zeilen) < 1000:
                try:
                    zeilen.append(self.schlange.get_nowait())
                except queue.Empty:
                    break
            if not zeilen:
                return
            stroeme: dict[tuple[str, str], list] = {}
            for (source, level), zeit, text in zeilen:
                stroeme.setdefault((source, level), []).append([zeit, text])
            daten = {"streams": [
                {"stream": {"app": APP, "host": self.host, "level": level, "source": source}, "values": werte}
                for (source, level), werte in stroeme.items()
            ]}
            try:
                antwort = self.client.post(self.url, content=json.dumps(daten),
                                           headers={"Content-Type": "application/json"})
                antwort.raise_for_status()
            except httpx.HTTPError as e:
                LOKI_VERWORFEN.inc(len(zeilen))
                # Nicht über logging melden, sonst entstünde für jeden Fehlversuch eine neue Zeile
                if self._zuletzt_gewarnt is None or time.monotonic() - self._zuletzt_gewarnt > 300:
                    self._zuletzt_gewarnt = time.monotonic()
                    self.warnen(f"Loki unter {self.url} nicht erreichbar ({e}); Protokollzeilen werden verworfen")
                return

    def close(self) -> None:
        self._stopp.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        self.senden()
        super().close()


def log_config(level: str = "INFO", loki_url: str = "", loki_host: str = "") -> dict:
    """Konfiguration für logging.config.dictConfig (an uvicorn.run übergeben).

    Alle Zeilen, auch die von uvicorn, gehen über den Root-Logger: ins Journal, in den Zähler und, wenn
    LOKI_URL gesetzt ist, an Loki.
    """
    handlers: dict[str, dict] = {
        "konsole": {"class": "logging.StreamHandler", "formatter": "konsole", "stream": "ext://sys.stderr"},
        "zaehler": {"()": f"{__name__}.Zeilenzaehler"},
    }
    if loki_url:
        handlers["loki"] = {"()": f"{__name__}.LokiHandler", "formatter": "loki", "url": loki_url,
                            "host": loki_host}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "konsole": {"format": "%(levelname)s %(name)s: %(message)s"},
            "loki": {"format": "%(message)s"},
        },
        "filters": {"ohne_metrics": {"()": f"{__name__}.OhneMetrics"}},
        "handlers": handlers,
        "root": {"level": level.upper() or "INFO", "handlers": list(handlers)},
        "loggers": {
            "uvicorn": {"level": "INFO", "handlers": [], "propagate": True},
            "uvicorn.error": {"level": "INFO", "handlers": [], "propagate": True},
            "uvicorn.access": {"level": "INFO", "handlers": [], "propagate": True, "filters": ["ohne_metrics"]},
            # httpx meldet jede Anfrage (auch an Paperless) auf INFO; das gehört nicht ins Protokoll
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }
