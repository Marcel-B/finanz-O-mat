"""Nummern aus dem Verwendungszweck in den Belegen wiederfinden.

Im Verwendungszweck stehen oft Rechnungs- oder Kundennummern (Telekom etwa
"Festnetz Vertragskonto 1234567890 RG 9876543210/17.12.2025"). Auf der Rechnung
sind sie meist in Gruppen gesetzt ("Buchungskonto 123 456 7890"). Deshalb
merken wir uns je Beleg alle Ziffernfolgen aus dem Dokumenttext, auch über
solche Lücken hinweg zusammengesetzt, und vergleichen nur ganze Folgen.

Den Text holen wir bei Bedarf nach (Paperless in einem Rutsch, Verzeichnis aus
der Datei) und legen nur die Nummern in der Datenbank ab.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import Config
from .db import Datenbank
from .models import Beleg, Umsatz

log = logging.getLogger(__name__)

MIN_STELLEN = 6  # kürzere Zahlen (PLZ, Jahreszahlen, Beträge) träfen zu viele Belege
MAX_STELLEN = 24
RE_FOLGE = re.compile(r"(?<![\w])\d+(?:[ \u00a0-]\d+)*(?![\w])")
RE_ZWECK = re.compile(rf"(?<!\d)\d{{{MIN_STELLEN},{MAX_STELLEN}}}(?!\d)")
PAUSE = 300  # Sekunden ohne neuen Versuch, wenn Paperless nicht antwortet

_pause_bis = 0.0


def nummern(text: str) -> set[str]:
    """Alle Nummern im Text; Gruppen wie "123 456 7890" auch zusammengesetzt."""
    gefunden: set[str] = set()
    for m in RE_FOLGE.finditer(text):
        gruppen = re.split(r"[ \u00a0-]", m.group())
        for i in range(len(gruppen)):
            zahl = ""
            for gruppe in gruppen[i:]:
                zahl += gruppe
                if len(zahl) > MAX_STELLEN:
                    break
                if len(zahl) >= MIN_STELLEN:
                    gefunden.add(zahl)
    return gefunden


def nummern_im_zweck(u: Umsatz) -> list[str]:
    """Nummern aus dem Verwendungszweck, in der Reihenfolge, in der sie dort stehen."""
    return list(dict.fromkeys(RE_ZWECK.findall(u.verwendungszweck)))


def _dokument(b: Beleg) -> tuple[str, str]:
    """Steuerposten derselben Rechnung ("abc#2") zählen als ein Dokument."""
    return (b.quelle, b.quelle_ref.split("#")[0]) if b.quelle_ref else ("id", str(b.id))


def beleg_nummern(b: Beleg, aus_text: dict[int, set[str]]) -> set[str]:
    return aus_text.get(b.id, set()) | nummern(f"{b.rechnungsnummer} {b.beschreibung}")


@dataclass
class Treffer:
    nummer: str
    eindeutig: bool  # steht nur in diesem Beleg (z. B. Rechnungsnummer, nicht Kundennummer)

    def to_dict(self) -> dict:
        return {"nummer": self.nummer, "eindeutig": self.eindeutig}


def treffer(u: Umsatz, belege: list[Beleg], aus_text: dict[int, set[str]]) -> dict[int, list[Treffer]]:
    """Welche Nummern aus dem Verwendungszweck in welchem Beleg stehen."""
    gesucht = nummern_im_zweck(u)
    if not gesucht:
        return {}
    je_beleg = {b.id: beleg_nummern(b, aus_text) & set(gesucht) for b in belege}
    dokumente: dict[str, set[tuple[str, str]]] = {n: set() for n in gesucht}
    for b in belege:
        for n in je_beleg[b.id]:
            dokumente[n].add(_dokument(b))
    return {b.id: [Treffer(n, len(dokumente[n]) == 1) for n in gesucht if n in je_beleg[b.id]]
            for b in belege if je_beleg[b.id]}


def nachtragen(db: Datenbank, cfg: Config, belege: list[Beleg], client: httpx.Client | None = None) -> None:
    """Liest die Nummern der Belege nach, für die noch keine gespeichert sind."""
    global _pause_bis
    bekannt = db.beleg_nummern_ids()
    fehlen = [b for b in belege if b.id not in bekannt]
    if not fehlen:
        return
    neu: dict[int, str] = {}
    paperless = [b for b in fehlen if b.quelle == "paperless"]
    if paperless and cfg.paperless_aktiv and time.monotonic() >= _pause_bis:
        try:
            texte = _paperless_texte(cfg, {b.quelle_ref.split("#")[0] for b in paperless}, client)
        except Exception as e:  # Paperless gerade nicht erreichbar: später noch einmal
            log.warning("Nummern aus Paperless nicht lesbar: %s", e)
            _pause_bis = time.monotonic() + PAUSE
        else:
            for b in paperless:
                neu[b.id] = " ".join(sorted(nummern(texte.get(b.quelle_ref.split("#")[0], ""))))
    for b in fehlen:
        if b.quelle == "verzeichnis":
            neu[b.id] = " ".join(sorted(nummern(_verzeichnis_text(cfg, b))))
        elif b.quelle != "paperless":
            neu[b.id] = ""  # manuell angelegt: nur die Felder des Belegs zählen
    db.beleg_nummern_speichern(neu)


def _paperless_texte(cfg: Config, refs: set[str], client: httpx.Client | None) -> dict[str, str]:
    from .quellen.paperless import PaperlessClient

    pl = PaperlessClient(cfg.paperless_url, cfg.paperless_token, client or httpx.Client(timeout=15))
    ids = sorted((r for r in refs if r.isdigit()), key=int)
    texte: dict[str, str] = {}
    for i in range(0, len(ids), 100):
        for dok in pl.alle_dokumente({"id__in": ",".join(ids[i:i + 100]), "fields": "id,content"}):
            texte[str(dok["id"])] = dok.get("content") or ""
    return texte


def _verzeichnis_text(cfg: Config, b: Beleg) -> str:
    from .quellen.verzeichnis import text_lesen

    if not cfg.rechnungs_verzeichnis or not b.quelle_link:
        return ""
    basis = cfg.rechnungs_verzeichnis.resolve()
    pfad = (basis / b.quelle_link).resolve()
    if not pfad.is_relative_to(basis) or not pfad.is_file() or pfad.suffix.lower() not in (".pdf", ".txt", ".xml"):
        return ""
    try:
        return pfad.read_text(encoding="utf-8", errors="replace") if pfad.suffix.lower() == ".xml" \
            else text_lesen(pfad)
    except Exception as e:  # eine kaputte Datei soll die Suche nicht aufhalten
        log.warning("Text von %s nicht lesbar: %s", pfad, e)
        return ""
