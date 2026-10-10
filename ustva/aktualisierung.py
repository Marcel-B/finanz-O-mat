"""Hinweis auf neue Versionen und Update auf Knopfdruck (Betrieb im Proxmox-Container).

Die App selbst installiert nichts. Im Container prüft ein systemd-Timer regelmäßig, ob es auf GitHub
einen neueren Stand gibt, und schreibt das Ergebnis nach ``<UPDATE_VERZEICHNIS>/status.json``.
Fordert man in der Oberfläche das Update an, legt die App nur die Datei ``anfordern`` an; eine
systemd-Path-Unit startet daraufhin das Update als root (deploy/proxmox/ct-setup.sh).
Ohne ``UPDATE_VERZEICHNIS`` (z. B. lokal oder mit Docker) ist die Funktion aus.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

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
        "zustand": daten.get("zustand", "unbekannt"),  # aktuell, neu, laeuft, fehler, unbekannt
        "installiert": daten.get("installiert"),
        "installiert_datum": daten.get("installiert_datum"),
        "neu": daten.get("neu"),
        "aenderungen": daten.get("aenderungen", []),
        "geprueft": daten.get("geprueft"),
        "meldung": daten.get("meldung", ""),
        "automatisch": bool(daten.get("automatisch", False)),
    }
    if (verzeichnis / ANFORDERN_DATEI).exists() and ergebnis["zustand"] != "laeuft":
        ergebnis["zustand"] = "angefordert"
    return ergebnis


def router(verzeichnis: Path | None) -> APIRouter:
    r = APIRouter()

    @r.get("/api/update")
    def update_status():
        return lesen(verzeichnis)

    @r.post("/api/update", status_code=202)
    def update_anfordern():
        status = lesen(verzeichnis)
        if not status["unterstuetzt"]:
            raise HTTPException(409, "Updates aus der App gibt es nur im Proxmox-Container")
        if status["zustand"] in {"laeuft", "angefordert"}:
            return status
        (verzeichnis / ANFORDERN_DATEI).touch()
        return lesen(verzeichnis)

    return r
