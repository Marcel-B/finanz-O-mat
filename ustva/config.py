"""Konfiguration über Umgebungsvariablen (siehe .env.example)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "ja", "yes", "on"}


def _liste(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [v.strip() for v in value.split(",") if v.strip()]


def _sicher(adresse: str) -> bool:
    """https://, oder http:// auf dem eigenen Rechner (Entwicklung)."""
    if adresse.startswith("https://"):
        return True
    host = urlsplit(adresse).hostname or ""
    return adresse.startswith("http://") and host in {"localhost", "127.0.0.1", "::1"}


@dataclass
class Config:
    datenbank: Path = Path("data/finanzomat.sqlite")
    # "ist": Zahlungseingang zählt (nur Ausgangsrechnungen), "soll": Rechnungsdatum zählt
    versteuerung: str = "ist"
    dauerfristverlaengerung: bool = False
    bundesland: str = ""  # z. B. "NW"; für Feiertage, die die Frist verschieben
    # Urlaub: ICS-Adresse eines Kalenders (z. B. geteilter Nextcloud-Kalender), Stichwörter im Titel
    kalender_url: str = ""
    kalender_stichwort: str = ""
    eigene_ustid: str = ""

    rechnungs_verzeichnis: Path | None = None
    ordner_ausgang: list[str] = field(default_factory=lambda: ["ausgang", "ausgangsrechnungen", "einnahmen"])
    ordner_eingang: list[str] = field(default_factory=lambda: ["eingang", "eingangsrechnungen", "ausgaben"])

    paperless_url: str = ""
    paperless_token: str = ""
    paperless_tags_ausgang: list[str] = field(default_factory=lambda: ["Ausgangsrechnung"])
    paperless_tags_eingang: list[str] = field(default_factory=lambda: ["Eingangsrechnung"])
    paperless_feld_netto: str = "Netto"
    paperless_feld_brutto: str = "Brutto"
    paperless_feld_steuer: str = "Umsatzsteuer"
    paperless_feld_steuersatz: str = "Steuersatz"
    paperless_feld_zahlungsdatum: str = "Zahlungsdatum"
    # Für den Monats-Export an die Steuerberatung
    paperless_tag_kontoauszug: str = "Kontoauszug"
    paperless_tag_protokoll: str = "UStVA-Protokoll"

    # Kontoumsätze, die keinen Beleg brauchen (Teilwort in Partner oder Verwendungszweck)
    bank_ignorieren: list[str] = field(default_factory=lambda: ["Finanzamt", "Bundeskasse", "Steuerverwaltung"])

    ezb_kurse: bool = True  # Monatsmittel der EZB-Referenzkurse abrufen, wenn kein Kurs hinterlegt ist

    app_benutzer: str = ""
    app_passwort: str = ""

    # Anmeldung über den Identity-Server (OpenID Connect, siehe ustva/identity.py). Ist die Authority
    # gesetzt, gilt nur noch diese Anmeldung; ohne sie wieder APP_BENUTZER/APP_PASSWORT (Notausgang).
    identity_authority: str = ""
    identity_client_id: str = "finanz-o-mat"
    identity_client_secret: str = ""
    identity_rolle: str = "finanzen"
    app_adresse: str = ""  # öffentliche Adresse der App, z. B. https://finanz-o-mat.<tailnet>.ts.net

    # Austausch mit dem Update-Dienst im Proxmox-Container (siehe ustva/aktualisierung.py)
    update_verzeichnis: Path | None = None
    # Stand des Nextcloud-Backups im Proxmox-Container (siehe ustva/backup.py)
    backup_verzeichnis: Path | None = None

    # Protokoll an Loki (leer = nur ins eigene Log), Label host, Stufe für das eigene Log
    loki_url: str = ""
    loki_host: str = ""
    log_level: str = "INFO"

    @classmethod
    def aus_umgebung(cls) -> Config:
        env = os.environ
        verzeichnis = env.get("RECHNUNGS_VERZEICHNIS")
        cfg = cls(
            datenbank=Path(env.get("DATENBANK", "data/finanzomat.sqlite")),
            versteuerung=env.get("VERSTEUERUNG", "ist").strip().lower(),
            dauerfristverlaengerung=_bool(env.get("DAUERFRISTVERLAENGERUNG")),
            bundesland=env.get("BUNDESLAND", "").strip().upper(),
            eigene_ustid=env.get("EIGENE_USTID", "").replace(" ", "").upper(),
            kalender_url=env.get("KALENDER_URL", "").strip(),
            kalender_stichwort=env.get("KALENDER_STICHWORT", "").strip(),
            rechnungs_verzeichnis=Path(verzeichnis) if verzeichnis else None,
            ordner_ausgang=_liste(env.get("ORDNER_AUSGANG"), cls().ordner_ausgang),
            ordner_eingang=_liste(env.get("ORDNER_EINGANG"), cls().ordner_eingang),
            paperless_url=env.get("PAPERLESS_URL", "").rstrip("/"),
            paperless_token=env.get("PAPERLESS_TOKEN", ""),
            paperless_tags_ausgang=_liste(env.get("PAPERLESS_TAGS_AUSGANG"), cls().paperless_tags_ausgang),
            paperless_tags_eingang=_liste(env.get("PAPERLESS_TAGS_EINGANG"), cls().paperless_tags_eingang),
            paperless_feld_netto=env.get("PAPERLESS_FELD_NETTO", cls.paperless_feld_netto),
            paperless_feld_brutto=env.get("PAPERLESS_FELD_BRUTTO", cls.paperless_feld_brutto),
            paperless_feld_steuer=env.get("PAPERLESS_FELD_STEUER", cls.paperless_feld_steuer),
            paperless_feld_steuersatz=env.get("PAPERLESS_FELD_STEUERSATZ", cls.paperless_feld_steuersatz),
            paperless_feld_zahlungsdatum=env.get("PAPERLESS_FELD_ZAHLUNGSDATUM", cls.paperless_feld_zahlungsdatum),
            paperless_tag_kontoauszug=env.get("PAPERLESS_TAG_KONTOAUSZUG", cls.paperless_tag_kontoauszug),
            paperless_tag_protokoll=env.get("PAPERLESS_TAG_PROTOKOLL", cls.paperless_tag_protokoll),
            bank_ignorieren=_liste(env.get("BANK_IGNORIEREN"), cls().bank_ignorieren),
            ezb_kurse=_bool(env.get("EZB_KURSE"), True),
            app_benutzer=env.get("APP_BENUTZER", ""),
            app_passwort=env.get("APP_PASSWORT", ""),
            identity_authority=env.get("IDENTITY_AUTHORITY", "").strip().rstrip("/"),
            identity_client_id=env.get("IDENTITY_CLIENT_ID", "").strip() or cls.identity_client_id,
            identity_client_secret=env.get("IDENTITY_CLIENT_SECRET", ""),
            identity_rolle=env.get("IDENTITY_ROLLE", "").strip() or cls.identity_rolle,
            app_adresse=env.get("APP_ADRESSE", "").strip().rstrip("/"),
            update_verzeichnis=Path(env["UPDATE_VERZEICHNIS"]) if env.get("UPDATE_VERZEICHNIS") else None,
            backup_verzeichnis=Path(env["BACKUP_VERZEICHNIS"]) if env.get("BACKUP_VERZEICHNIS") else None,
            loki_url=env.get("LOKI_URL", "").strip(),
            loki_host=env.get("LOKI_HOST", "").strip() or socket.gethostname(),
            log_level=env.get("LOG_LEVEL", "").strip().upper() or "INFO",
        )
        if cfg.versteuerung not in {"soll", "ist"}:
            raise ValueError("VERSTEUERUNG muss 'soll' oder 'ist' sein")
        cfg.identity_pruefen()
        return cfg

    @property
    def identity_aktiv(self) -> bool:
        return bool(self.identity_authority)

    def identity_pruefen(self) -> None:
        """Wirft ValueError, wenn die Identity-Anmeldung halb eingerichtet ist."""
        if not self.identity_aktiv:
            return
        fehlt = [n for n, w in (("IDENTITY_CLIENT_SECRET", self.identity_client_secret),
                                ("APP_ADRESSE", self.app_adresse)) if not w]
        if fehlt:
            raise ValueError(f"Mit IDENTITY_AUTHORITY auch {' und '.join(fehlt)} setzen")
        for name, wert in (("IDENTITY_AUTHORITY", self.identity_authority), ("APP_ADRESSE", self.app_adresse)):
            if not _sicher(wert):
                raise ValueError(f"{name} muss mit https:// beginnen (http:// nur für localhost)")

    @property
    def paperless_aktiv(self) -> bool:
        return bool(self.paperless_url and self.paperless_token)
