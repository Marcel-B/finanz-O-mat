"""Doppelte Rechnungen in Paperless finden und aufräumen.

Dieselbe Rechnung landet leicht mehrfach in Paperless: aus der Mail, per Hand hochgeladen,
aus der sevDesk-Übernahme. Die Dateien unterscheiden sich, deshalb erkennt Paperless sie
nicht. Verglichen wird wie beim Einlesen in finanz-O-mat (siehe doppelt.py), was auf der
Rechnung steht: Art, Datum, Betrag und Rechnungsnummer. Zusätzlich gelten Dokumente mit
gleichem Text als gleich.

Behalten wird je Gruppe das Dokument, auf das ein Beleg in finanz-O-mat verweist, sonst das
älteste. Es bekommt die Tags der anderen, die anderen werden gelöscht (Paperless legt sie in
den Papierkorb).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config
from .db import Datenbank
from .doppelt import beschreibung, gleich
from .models import Beleg
from .quellen.paperless import PaperlessClient, text_beleg
from .steuerfaelle import Art

MIN_TEXT = 80  # kürzere Texte (leere Scans, nur Logo) taugen nicht zum Vergleich


@dataclass
class Gruppe:
    behalten: dict
    loeschen: list[dict]
    beleg: Beleg
    tags_dazu: list[int] = field(default_factory=list)
    hinweis: str = ""  # gesetzt, wenn die Gruppe nicht automatisch aufgeräumt werden kann


def _text(dok: dict) -> str:
    return re.sub(r"\s+", " ", (dok.get("content") or "").lower()).strip()


def _art(dok: dict, tags: dict[int, str], typen: dict[int, str], cfg: Config) -> Art | None:
    namen = {tags.get(t, "").lower() for t in dok.get("tags", [])}
    if dok.get("document_type") is not None:
        namen.add(typen.get(dok["document_type"], "").lower())
    if namen & {n.lower() for n in cfg.paperless_tags_ausgang}:
        return Art.AUSGANG
    if namen & {n.lower() for n in cfg.paperless_tags_eingang}:
        return Art.EINGANG
    return None


def gruppieren(dokumente: list[tuple[dict, Beleg]]) -> list[list[int]]:
    """Indizes der Dokumente, die dieselbe Rechnung sind (nur Gruppen ab zwei)."""
    eltern = list(range(len(dokumente)))

    def wurzel(i: int) -> int:
        while eltern[i] != i:
            eltern[i] = eltern[eltern[i]]
            i = eltern[i]
        return i

    def verbinden(i: int, j: int) -> None:
        eltern[wurzel(j)] = wurzel(i)

    nach_betrag: dict[tuple, list[int]] = {}
    nach_text: dict[str, list[int]] = {}
    for i, (dok, beleg) in enumerate(dokumente):
        if beleg.zahlbetrag():
            nach_betrag.setdefault((beleg.art, beleg.zahlbetrag()), []).append(i)
        if len(text := _text(dok)) >= MIN_TEXT:
            nach_text.setdefault(text, []).append(i)
    for kandidaten in nach_betrag.values():
        for n, i in enumerate(kandidaten):
            for j in kandidaten[n + 1:]:
                if gleich(dokumente[i][1], dokumente[j][1]):
                    verbinden(i, j)
    for kandidaten in nach_text.values():
        for j in kandidaten[1:]:
            verbinden(kandidaten[0], j)

    gruppen: dict[int, list[int]] = {}
    for i in range(len(dokumente)):
        gruppen.setdefault(wurzel(i), []).append(i)
    return sorted((sorted(g) for g in gruppen.values() if len(g) > 1), key=lambda g: g[0])


def finden(pl: PaperlessClient, cfg: Config, db: Datenbank, von: str | None = None) -> list[Gruppe]:
    tags = pl.namen("/api/tags/")
    typen = pl.namen("/api/document_types/")
    korrespondenten = pl.namen("/api/correspondents/")
    felder = pl.felder()
    params = {"ordering": "created"}
    if von:
        params["created__gte"] = f"{von}-01"
    dokumente = []
    for dok in pl.alle_dokumente(params):
        if art := _art(dok, tags, typen, cfg):
            dokumente.append((dok, text_beleg(dok, art, cfg, felder, korrespondenten)))

    in_finanzomat: dict[int, int] = {}  # Paperless-Dokument -> Zahl der Belege, die darauf verweisen
    for b in db.alle_belege():
        if b.quelle == "paperless" and b.quelle_ref and "#" not in b.quelle_ref and b.quelle_ref.isdigit():
            in_finanzomat[int(b.quelle_ref)] = in_finanzomat.get(int(b.quelle_ref), 0) + 1

    ergebnis = []
    for indizes in gruppieren(dokumente):
        doks = [dokumente[i][0] for i in indizes]
        verwiesen = [d for d in doks if d["id"] in in_finanzomat]
        doks.sort(key=lambda d: (d["id"] not in in_finanzomat, d["id"]))
        behalten, rest = doks[0], doks[1:]
        vorhanden = set(behalten.get("tags", []))
        dazu = sorted({t for d in rest for t in d.get("tags", [])} - vorhanden)
        gruppe = Gruppe(behalten, rest, dokumente[indizes[0]][1], dazu)
        if len(verwiesen) > 1:
            gruppe.hinweis = (f"In finanz-O-mat gibt es zu {len(verwiesen)} dieser Dokumente einen Beleg. "
                              "Erst dort den doppelten Beleg löschen, dann erneut prüfen.")
        ergebnis.append(gruppe)
    return ergebnis


def aufraeumen(pl: PaperlessClient, gruppe: Gruppe) -> None:
    """Tags auf das behaltene Dokument übertragen, dann die anderen löschen."""
    if gruppe.hinweis:
        return
    if gruppe.tags_dazu:
        pl.senden("PATCH", f"/api/documents/{gruppe.behalten['id']}/",
                  {"tags": sorted(set(gruppe.behalten.get("tags", [])) | set(gruppe.tags_dazu))})
    pl.senden("POST", "/api/documents/bulk_edit/",
              {"documents": [d["id"] for d in gruppe.loeschen], "method": "delete", "parameters": {}})


def beschreiben(gruppe: Gruppe, tags: dict[int, str]) -> list[str]:
    def zeile(dok: dict) -> str:
        namen = ", ".join(tags.get(t, str(t)) for t in dok.get("tags", []))
        return f"#{dok['id']} „{dok.get('title') or ''}“ (hinzugefügt {str(dok.get('added', ''))[:10]}; {namen})"

    zeilen = [beschreibung(gruppe.beleg), f"  behalten  {zeile(gruppe.behalten)}"]
    zeilen += [f"  löschen   {zeile(d)}" for d in gruppe.loeschen]
    if gruppe.tags_dazu:
        zeilen.append("  Tags dazu: " + ", ".join(tags.get(t, str(t)) for t in gruppe.tags_dazu))
    if gruppe.hinweis:
        zeilen.append(f"  übersprungen: {gruppe.hinweis}")
    return zeilen
