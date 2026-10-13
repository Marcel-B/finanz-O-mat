"""Stand des Nextcloud-Backups und „Jetzt sichern“ (Betrieb im Proxmox-Container).

Wie bei den Updates (siehe aktualisierung.py) sichert die App nichts selbst: Im Container läuft täglich
deploy/proxmox/ct-backup.sh und schreibt den Stand nach ``<BACKUP_VERZEICHNIS>/status.json``. „Jetzt sichern“
legt nur die Datei ``anfordern`` an, eine systemd-Path-Unit startet dann das Backup als root.
Ohne ``BACKUP_VERZEICHNIS`` ist die Funktion aus.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

STATUS_DATEI = "status.json"
ANFORDERN_DATEI = "anfordern"


def lesen(verzeichnis: Path | None) -> dict:
    if not verzeichnis or not verzeichnis.is_dir():
        return {"unterstuetzt": False}
    try:
        daten = json.loads((verzeichnis / STATUS_DATEI).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        daten = {}
    ergebnis = {
        "unterstuetzt": True,
        "zustand": daten.get("zustand", "aus"),  # aus, laeuft, ok, fehler
        "meldung": daten.get("meldung", ""),
        "ziel": daten.get("ziel"),
        "letzte_sicherung": daten.get("letzte_sicherung"),
        "letzter_versuch": daten.get("letzter_versuch"),
        "anzahl": daten.get("anzahl"),
        "groesse": daten.get("groesse"),
    }
    if (verzeichnis / ANFORDERN_DATEI).exists() and ergebnis["zustand"] != "laeuft":
        ergebnis["zustand"] = "angefordert"
    return ergebnis


def router(verzeichnis: Path | None) -> APIRouter:
    r = APIRouter()

    @r.get("/api/backup")
    def backup_status():
        return lesen(verzeichnis)

    @r.post("/api/backup", status_code=202)
    def backup_anfordern():
        status = lesen(verzeichnis)
        if not status["unterstuetzt"]:
            raise HTTPException(409, "Backups aus der App gibt es nur im Proxmox-Container")
        if status["zustand"] == "aus":
            raise HTTPException(409, "Backup ist nicht eingerichtet (im Container: finanz-o-mat-backup einrichten)")
        if status["zustand"] not in {"laeuft", "angefordert"}:
            (verzeichnis / ANFORDERN_DATEI).touch()
            log.info("Backup in der App angefordert")
        return lesen(verzeichnis)

    return r
