"""Konfiguration über Umgebungsvariablen (siehe .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "ja", "yes", "on"}


def _liste(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Config:
    datenbank: Path = Path("data/finanzomat.sqlite")
    # "ist": Zahlungseingang zählt (nur Ausgangsrechnungen), "soll": Rechnungsdatum zählt
    versteuerung: str = "ist"
    dauerfristverlaengerung: bool = False
    bundesland: str = ""  # z. B. "NW"; für Feiertage, die die Frist verschieben
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

    ezb_kurse: bool = True  # Monatsmittel der EZB-Referenzkurse abrufen, wenn kein Kurs hinterlegt ist

    app_benutzer: str = ""
    app_passwort: str = ""

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
            ezb_kurse=_bool(env.get("EZB_KURSE"), True),
            app_benutzer=env.get("APP_BENUTZER", ""),
            app_passwort=env.get("APP_PASSWORT", ""),
        )
        if cfg.versteuerung not in {"soll", "ist"}:
            raise ValueError("VERSTEUERUNG muss 'soll' oder 'ist' sein")
        return cfg

    @property
    def paperless_aktiv(self) -> bool:
        return bool(self.paperless_url and self.paperless_token)
