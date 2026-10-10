"""ZIP mit allen Unterlagen eines Monats für die Steuerberatung.

Enthalten sind die Belege, die in der Voranmeldung des Monats zählen oder einem
Kontoumsatz des Monats zugeordnet sind (als Originaldatei aus dem
Rechnungsverzeichnis bzw. als PDF aus Paperless), dazu Kontoauszug und
ELSTER-Übertragungsprotokoll aus Paperless, die CSV-Exporte und eine Liste,
was fehlt.

Kontoauszug und Protokoll werden über je einen Tag gefunden. Weil das Protokoll
erst im Folgemonat entsteht, zählt nicht das Dokumentdatum, sondern dass Titel
oder Text den Monat nennen (z. B. "09/2026", "September 2026").
"""

from __future__ import annotations

import io
import mimetypes
import re
import zipfile
from datetime import date

import httpx

from . import export
from .berechnung import Bericht
from .config import Config
from .fristen import monat_plus
from .models import Beleg, Umsatz
from .quellen.paperless import PaperlessClient, PaperlessFehler

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _sicher(text: str, laenge: int = 60) -> str:
    text = re.sub(r"[^\w.,+-]+", "-", text, flags=re.UNICODE).strip("-.")
    return text[:laenge].rstrip("-.") or "Dokument"


def _betrag(b: Beleg) -> str:
    return f"{abs(b.zahlbetrag()):.2f}".replace(".", ",") + b.waehrung


def beleg_dateiname(b: Beleg, endung: str) -> str:
    teile = [f"{b.rechnungsdatum:%Y-%m-%d}" if b.rechnungsdatum else "ohne-Datum",
             "Ausgang" if b.art.value == "ausgang" else "Eingang",
             _sicher(b.partner or b.beschreibung or "Beleg", 40), _betrag(b)]
    return "_".join(teile) + endung


def monat_genannt(text: str, jahr: int, monat: int) -> bool:
    text = text.lower()
    muster = [f"{monat:02d}/{jahr}", f"{monat:02d}.{jahr}", f"{jahr}-{monat:02d}", f"{monat}/{jahr}",
              f"{MONATE[monat - 1]} {jahr}", f"{MONTHS[monat - 1]} {jahr}"]
    return any(re.search(rf"(?<!\d){re.escape(m.lower())}", text) for m in muster)


def _endung(antwort: httpx.Response, standard: str = ".pdf") -> str:
    if m := re.search(r'filename="?([^";]+)"?', antwort.headers.get("content-disposition", "")):
        if endung := re.search(r"\.[A-Za-z0-9]{1,5}$", m.group(1)):
            return endung.group(0).lower()
    typ = antwort.headers.get("content-type", "").split(";")[0].strip()
    return mimetypes.guess_extension(typ) or standard if typ else standard


class _Zip:
    def __init__(self) -> None:
        self.puffer = io.BytesIO()
        self.datei = zipfile.ZipFile(self.puffer, "w", zipfile.ZIP_DEFLATED)
        self.namen: set[str] = set()

    def schreiben(self, name: str, inhalt: bytes | str) -> str:
        basis, punkt, endung = name.rpartition(".")
        if not punkt:
            basis, endung = name, ""
        kandidat, n = name, 2
        while kandidat in self.namen:
            kandidat = f"{basis}-{n}.{endung}" if endung else f"{basis}-{n}"
            n += 1
        self.namen.add(kandidat)
        self.datei.writestr(kandidat, inhalt)
        return kandidat

    def fertig(self) -> bytes:
        self.datei.close()
        return self.puffer.getvalue()


def monats_zip(cfg: Config, bericht: Bericht, umsaetze: list[Umsatz], belege_nach_id: dict[int, Beleg],
               client: httpx.Client | None = None) -> bytes:
    jahr, monat = int(bericht.zeitraum[:4]), int(bericht.zeitraum[5:])
    z = _Zip()
    fehlt: list[str] = []

    # Belege der Voranmeldung, dann die über Kontoumsätze zugeordneten aus anderen Monaten
    belege: dict[int, Beleg] = {zeile.beleg.id: zeile.beleg for zeile in bericht.zeilen if zeile.beleg.id}
    for u in umsaetze:
        if u.beleg_id in belege_nach_id:
            belege.setdefault(u.beleg_id, belege_nach_id[u.beleg_id])

    pl = PaperlessClient(cfg.paperless_url, cfg.paperless_token, client) if cfg.paperless_aktiv else None
    for b in belege.values():
        try:
            if b.quelle == "verzeichnis" and cfg.rechnungs_verzeichnis:
                basis = cfg.rechnungs_verzeichnis.resolve()
                pfad = (basis / b.quelle_link).resolve()
                if not pfad.is_relative_to(basis) or not pfad.is_file():
                    raise FileNotFoundError("Datei nicht mehr vorhanden")
                z.schreiben(f"Belege/{beleg_dateiname(b, pfad.suffix.lower())}", pfad.read_bytes())
            elif b.quelle == "paperless" and pl is not None:
                antwort = pl.herunterladen(int(b.quelle_ref.split("#")[0]))
                z.schreiben(f"Belege/{beleg_dateiname(b, _endung(antwort))}", antwort.content)
            else:
                raise ValueError("keine Datei hinterlegt (manuell angelegt)")
        except Exception as e:  # ein fehlender Beleg soll den Export nicht verhindern
            fehlt.append(f"Beleg {beleg_dateiname(b, '')}: {e}")

    # Der Auszug entsteht am Monatsende, das Protokoll mit der Abgabe bis zu zwei Monate später. Das
    # Fenster hält den Auszug des Folgemonats heraus, der den Schlusssaldo vom Monatsletzten nennt.
    nj, nm = monat_plus(jahr, monat, 1)
    pj, pm = monat_plus(jahr, monat, 3)
    for tag, ordner, bezeichnung, bis in (
        (cfg.paperless_tag_kontoauszug, "Kontoauszug", "Kontoauszug", date(nj, nm, 16)),
        (cfg.paperless_tag_protokoll, "Finanzamt", "ELSTER-Protokoll", date(pj, pm, 1)),
    ):
        if pl is None or not tag:
            fehlt.append(f"{bezeichnung}: Paperless ist nicht eingerichtet, bitte selbst beilegen")
            continue
        try:
            treffer = _monatsdokumente(pl, tag, jahr, monat, bis)
            for dok in treffer:
                antwort = pl.herunterladen(dok["id"])
                tag_datum = str(dok.get("created") or "")[:10]
                z.schreiben(f"{ordner}/{tag_datum}_{_sicher(dok.get('title') or bezeichnung)}{_endung(antwort)}",
                            antwort.content)
            if not treffer:
                fehlt.append(f"{bezeichnung}: in Paperless kein Dokument mit Tag „{tag}“ für "
                             f"{monat:02d}/{jahr} gefunden")
        except (PaperlessFehler, httpx.HTTPError) as e:
            fehlt.append(f"{bezeichnung}: {e}")

    for u in umsaetze:
        if u.beleg_id is None and not u.ignoriert:
            betrag = f"{u.betrag:.2f}".replace(".", ",")
            fehlt.append(f"Kontoumsatz {u.buchungsdatum:%d.%m.%Y} {u.partner or u.verwendungszweck} {betrag} € "
                         "ohne Beleg")

    z.schreiben(f"UStVA-{bericht.zeitraum}-Kennzahlen.csv", export.kennzahlen_csv(bericht))
    z.schreiben(f"UStVA-{bericht.zeitraum}-Belege.csv", export.belege_csv(bericht))
    if fehlt:
        z.schreiben("FEHLT.txt", "\r\n".join([f"Unterlagen {monat:02d}/{jahr}: Was fehlt", "", *fehlt, ""]))
    return z.fertig()


def _monatsdokumente(pl: PaperlessClient, tag: str, jahr: int, monat: int, bis: date) -> list[dict]:
    tags = {name.lower(): tid for tid, name in pl.namen("/api/tags/").items()}
    if tag.lower() not in tags:
        return []
    kandidaten = pl.dokumente(date(jahr, monat, 1), bis, tag_id=tags[tag.lower()])
    return [d for d in kandidaten
            if monat_genannt(f"{d.get('title', '')}\n{d.get('content', '')}", jahr, monat)]
