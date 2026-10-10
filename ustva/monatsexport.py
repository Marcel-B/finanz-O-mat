"""ZIP mit allen Unterlagen eines Monats für die Steuerberatung.

Enthalten sind die Belege, die in der Voranmeldung des Monats zählen oder einem
Kontoumsatz des Monats zugeordnet sind (als Originaldatei aus dem
Rechnungsverzeichnis bzw. als PDF aus Paperless). Wurde ein Beleg der Voranmeldung
in einem anderen Monat abgebucht (z. B. Rechnung vom 22.09., Abbuchung am 01.10.),
vermerkt die INFO.txt das, weil er dann auf dem Kontoauszug fehlt. Dazu kommen Kontoauszug und
ELSTER-Übertragungsprotokoll aus Paperless, die CSV-Exporte und eine Liste,
was fehlt.

Kontoauszug und Protokoll werden über je einen Tag gefunden. Weil das Protokoll
erst im Folgemonat entsteht, zählt nicht das Dokumentdatum, sondern dass Titel
oder Text den Monat nennen (z. B. "09/2026", "September 2026"). Nennt ein
Protokoll mehrere Monate, gehört es zum frühesten: Das Übermittlungsdatum
("09.10.2026") liegt immer nach dem Zeitraum.
"""

from __future__ import annotations

import io
import mimetypes
import re
import zipfile
from datetime import date

import httpx

from . import bank, export
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


def _monatsnamen(monat: int) -> list[str]:
    namen = {MONATE[monat - 1], MONTHS[monat - 1], MONATE[monat - 1][:3], MONTHS[monat - 1][:3]}
    namen |= {"Maerz"} if monat == 3 else {"Sept"} if monat == 9 else set()
    return sorted((n.lower() for n in namen), key=len, reverse=True)


def monat_genannt(text: str, jahr: int, monat: int) -> bool:
    """Nennt der Text den Monat, als Zahl ("09/2026", "01.09.2026") oder als Name nahe beim Jahr?

    "Name nahe beim Jahr" deckt "September 2026", Dateinamen wie "UStVA_2026_-_September" und
    Formulare wie "Jahr 2026 / Zeitraum September" ab.
    """
    text = re.sub(r"[_\s]+", " ", text.lower())
    zahlen = [f"{monat:02d}/{jahr}", f"{monat:02d}.{jahr}", f"{jahr}-{monat:02d}", f"{monat}/{jahr}"]
    if any(re.search(rf"(?<!\d){re.escape(m)}", text) for m in zahlen):
        return True
    jahre = [m.start() for m in re.finditer(rf"(?<!\d){jahr}(?!\d)", text)]
    namen = [m.start() for m in re.finditer(rf"\b(?:{'|'.join(map(re.escape, _monatsnamen(monat)))})\b", text)]
    return any(abs(j - n) <= 40 for j in jahre for n in namen)


def _anderer_monat(text: str, jahr: int, monat: int) -> bool:
    return any(monat_genannt(text, jahr, m) for m in range(1, 13) if m != monat)


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
               client: httpx.Client | None = None, andere_umsaetze: list[Umsatz] = ()) -> bytes:
    """`umsaetze` sind die Kontoumsätze des Monats, `andere_umsaetze` die übrigen."""
    jahr, monat = int(bericht.zeitraum[:4]), int(bericht.zeitraum[5:])
    z = _Zip()
    fehlt: list[str] = []

    # Belege der Voranmeldung, dann die über Kontoumsätze zugeordneten aus anderen Monaten
    belege: dict[int, Beleg] = {zeile.beleg.id: zeile.beleg for zeile in bericht.zeilen if zeile.beleg.id}
    im_monat = {u.beleg_id for u in umsaetze if u.beleg_id}
    abgebucht = {u.beleg_id: u for u in andere_umsaetze if u.beleg_id}
    anderswo = [f"{beleg_dateiname(b, '')}: abgebucht am {abgebucht[i].buchungsdatum:%d.%m.%Y}"
                for i, b in belege.items() if i in abgebucht and i not in im_monat]
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
    for tag, ordner, bezeichnung, bis, protokoll in (
        (cfg.paperless_tag_kontoauszug, "Kontoauszug", "Kontoauszug", date(nj, nm, 16), False),
        (cfg.paperless_tag_protokoll, "Finanzamt", "ELSTER-Protokoll", date(pj, pm, 1), True),
    ):
        if pl is None or not tag:
            fehlt.append(f"{bezeichnung}: Paperless ist nicht eingerichtet, bitte selbst beilegen")
            continue
        try:
            treffer = _monatsdokumente(pl, tag, jahr, monat, bis, protokoll)
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

    fehlbuchungen: list[str] = []
    ohne_beleg: list[str] = []
    for u in umsaetze:
        if u.beleg_id is not None:
            continue
        if not u.ignoriert:
            fehlt.append(f"{_umsatz(u)} ohne Beleg")
        elif bank.ist_fehlbuchung(u):
            notiz = u.ignoriert_grund[len(bank.FEHLBUCHUNG):].lstrip(" :")
            fehlbuchungen.append(_umsatz(u) + (f": {notiz}" if notiz else ""))
        else:
            ohne_beleg.append(_umsatz(u) + (f" ({u.ignoriert_grund})" if u.ignoriert_grund else ""))

    z.schreiben(f"UStVA-{bericht.zeitraum}-Kennzahlen.csv", export.kennzahlen_csv(bericht))
    z.schreiben(f"UStVA-{bericht.zeitraum}-Belege.csv", export.belege_csv(bericht))
    abschnitte = [("Fehlende Unterlagen", fehlt),
                  ("In der Voranmeldung enthalten, aber in einem anderen Monat abgebucht", anderswo),
                  ("Fehlbuchungen / privat (nicht steuerrelevant)", fehlbuchungen),
                  ("Kontoumsätze ohne Beleg, kein Beleg nötig", ohne_beleg)]
    if fehlt or anderswo or fehlbuchungen:
        zeilen = [f"Unterlagen {monat:02d}/{jahr}", ""]
        for titel, eintraege in abschnitte:
            if eintraege:
                zeilen += [titel, "-" * len(titel), *(f"- {e}" for e in eintraege), ""]
        z.schreiben("INFO.txt", "\r\n".join(zeilen))
    return z.fertig()


def _umsatz(u: Umsatz) -> str:
    betrag = f"{u.betrag:.2f}".replace(".", ",")
    return f"Kontoumsatz {u.buchungsdatum:%d.%m.%Y} {u.partner or u.verwendungszweck} {betrag} €"


def protokoll_vorhanden(pl: PaperlessClient, tag: str, jahr: int, monat: int) -> bool:
    """Liegt in Paperless das ELSTER-Protokoll des Monats, ist die Voranmeldung abgegeben."""
    pj, pm = monat_plus(jahr, monat, 3)
    return bool(_monatsdokumente(pl, tag, jahr, monat, date(pj, pm, 1), protokoll=True))


def _monatsdokumente(pl: PaperlessClient, tag: str, jahr: int, monat: int, bis: date,
                     protokoll: bool = False) -> list[dict]:
    tags = {name.lower(): tid for tid, name in pl.namen("/api/tags/").items()}
    if tag.lower() not in tags:
        return []
    kandidaten = pl.dokumente(date(jahr, monat, 1), bis, tag_id=tags[tag.lower()])
    text = {d["id"]: f"{d.get('title', '')}\n{d.get('content', '')}" for d in kandidaten}
    treffer = [d for d in kandidaten if monat_genannt(text[d["id"]], jahr, monat)]
    if not protokoll:
        return treffer
    # Das Protokoll des Vormonats nennt mit dem Übermittlungsdatum auch diesen Monat
    frueher = [monat_plus(jahr, monat, -n) for n in (1, 2, 3)]
    treffer = [d for d in treffer if not any(monat_genannt(text[d["id"]], j, m) for j, m in frueher)]
    if treffer:
        return treffer
    # Sonst: in der ersten Monatshälfte danach erstellt (die Abgabe bis zum 10.) und kein anderer Monat genannt
    nj, nm = monat_plus(jahr, monat, 1)
    von, bis_folgemonat = date(nj, nm, 1).isoformat(), date(nj, nm, 16).isoformat()
    return [d for d in kandidaten if von <= str(d.get("created") or "")[:10] < bis_folgemonat
            and not _anderer_monat(text[d["id"]], jahr, monat)]
