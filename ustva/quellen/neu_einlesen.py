"""Bereits eingelesene, noch nicht geprüfte Belege erneut aus der Quelle lesen.

Nützlich, wenn die Texterkennung verbessert wurde. Geprüfte und manuell
angelegte Belege bleiben unberührt. Der Beleg behält seine ID, damit
Zuordnungen zu Kontoumsätzen erhalten bleiben; ein bekanntes Zahlungsdatum
geht nicht verloren.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..adressbuch import anschriften_uebernehmen
from ..config import Config
from ..db import Datenbank
from ..models import Beleg
from . import paperless, verzeichnis

QUELLEN = ("verzeichnis", "paperless")


@dataclass
class NeuEingelesen:
    aktualisiert: int = 0
    neu: int = 0
    fehler: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"aktualisiert": self.aktualisiert, "neu": self.neu, "fehler": self.fehler}


def kandidaten(db: Datenbank) -> list[Beleg]:
    return [b for b in db.alle_belege() if b.pruefen and b.quelle in QUELLEN]


def neu_einlesen(
    db: Datenbank, cfg: Config, beleg_ids: list[int] | None = None, client: httpx.Client | None = None
) -> NeuEingelesen:
    ergebnis = NeuEingelesen()
    belege = kandidaten(db)
    if beleg_ids is not None:
        gewuenscht = set(beleg_ids)
        belege = [b for b in belege if b.id in gewuenscht]
        for fehlt in gewuenscht - {b.id for b in belege}:
            ergebnis.fehler.append(f"Beleg {fehlt} ist geprüft, manuell angelegt oder nicht vorhanden")

    # Mehrere Steuerposten einer Rechnung (Referenz "abc#2") werden gemeinsam gelesen.
    gruppen: dict[tuple[str, str], list[Beleg]] = defaultdict(list)
    for b in belege:
        gruppen[(b.quelle, b.quelle_ref.split("#")[0])].append(b)

    pl: paperless.PaperlessClient | None = None
    namen: tuple[dict[int, str], dict[int, str]] | None = None
    for (quelle, ref), alte in gruppen.items():
        name = alte[0].beschreibung or alte[0].quelle_link
        try:
            if quelle == "verzeichnis":
                neue = _aus_verzeichnis(cfg, alte[0], ref)
            else:
                if pl is None or namen is None:
                    if not cfg.paperless_aktiv:
                        raise paperless.PaperlessFehler("Paperless ist nicht konfiguriert")
                    pl = paperless.PaperlessClient(cfg.paperless_url, cfg.paperless_token, client)
                    namen = (pl.felder(), pl.namen("/api/correspondents/"))
                dok = pl.dokument(int(ref))
                neue = paperless.dokument_belege(pl, dok, alte[0].art, cfg, *namen)
        except Exception as e:  # ein Beleg soll die übrigen nicht aufhalten
            ergebnis.fehler.append(f"{name}: {e}")
            continue
        _uebernehmen(db, alte, neue, ergebnis, nur_vorhandene=beleg_ids is not None)
    return ergebnis


def _aus_verzeichnis(cfg: Config, alt: Beleg, ref: str) -> list[Beleg]:
    if not cfg.rechnungs_verzeichnis:
        raise ValueError("Kein Rechnungsverzeichnis konfiguriert")
    basis = cfg.rechnungs_verzeichnis.resolve()
    pfad = (basis / alt.quelle_link).resolve()
    if not pfad.is_relative_to(basis) or not pfad.is_file():
        raise ValueError("Datei nicht mehr vorhanden")
    summe = verzeichnis.pruefsumme(pfad)
    if summe != ref:
        # Eine geänderte Datei liest der normale Import als neue Rechnung ein.
        raise ValueError("Datei wurde seit dem Einlesen geändert; Beleg löschen und neu einlesen")
    return verzeichnis.datei_belege(pfad, Path(alt.quelle_link), alt.art, cfg, summe)


def _uebernehmen(db: Datenbank, alte: list[Beleg], neue: list[Beleg], ergebnis: NeuEingelesen,
                 nur_vorhandene: bool) -> None:
    nach_ref = {b.quelle_ref: b for b in alte}
    anschriften_uebernehmen(db, neue)
    for neu in neue:
        alt = nach_ref.get(neu.quelle_ref)
        if alt is None:
            # Weiterer Steuerposten, den die alte Erkennung nicht gefunden hat.
            if not nur_vorhandene and not db.beleg_vorhanden(neu.quelle, neu.quelle_ref):
                db.beleg_anlegen(neu)
                ergebnis.neu += 1
            continue
        neu.id = alt.id
        neu.art = alt.art
        neu.zahlungsdatum = neu.zahlungsdatum or alt.zahlungsdatum
        neu.pruefen = True
        db.beleg_aktualisieren(neu)
        db.beleg_nummern_vergessen(neu.id)
        ergebnis.aktualisiert += 1
