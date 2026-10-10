"""Adressbuch: Kunden und Lieferanten aus Belegen, E-Rechnungen und dem sevDesk-Umzug.

Automatische Quellen füllen nur leere Felder; was von Hand eingetragen wurde, bleibt stehen. Ein Kontakt wird
über die USt-IdNr. oder den Namen (ohne Groß-/Kleinschreibung und Satzzeichen) wiedererkannt. Ausgeblendete
Kontakte bleiben gespeichert, damit sie beim nächsten Abgleich nicht wieder auftauchen.
"""

from __future__ import annotations

import csv
import io
import re

from .db import Datenbank
from .models import Beleg, Kontakt, schluessel
from .steuerfaelle import Art

ANSCHRIFT = ("strasse", "plz", "ort", "land")
FUELLBAR = ("kundennummer", "ustid", *ANSCHRIFT, "email", "telefon", "ansprechpartner")

LAENDER = {
    "deutschland": "DE", "österreich": "AT", "schweiz": "CH", "irland": "IE", "niederlande": "NL",
    "luxemburg": "LU", "belgien": "BE", "frankreich": "FR", "italien": "IT", "spanien": "ES", "polen": "PL",
    "tschechien": "CZ", "tschechische republik": "CZ", "dänemark": "DK", "schweden": "SE", "finnland": "FI",
    "vereinigte staaten von amerika": "US", "vereinigte staaten": "US", "usa": "US",
    "vereinigtes königreich": "GB", "großbritannien": "GB", "china": "CN", "hongkong": "HK", "kanada": "CA",
}


def land_code(text: str) -> str:
    t = text.strip()
    if re.fullmatch(r"[A-Za-z]{2}", t):
        return t.upper()
    return LAENDER.get(t.lower(), t)


def _rolle(alt: str, neu: str) -> str:
    if not alt or alt == neu or not neu:
        return alt or neu
    return "beides"


class Abgleich:
    """Ergänzt Kontakte; hält den Bestand im Speicher, damit viele Belege nicht viele Abfragen kosten."""

    def __init__(self, db: Datenbank):
        self.db = db
        self.bestand = db.kontakte()
        self.neu = 0
        self.ergaenzt = 0

    def _finden(self, k: Kontakt) -> Kontakt | None:
        ustid = k.ustid.replace(" ", "").upper()
        for alt in self.bestand:
            if ustid and alt.ustid.replace(" ", "").upper() == ustid:
                return alt
        s = k.schluessel
        return next((alt for alt in self.bestand if s in alt.schluessel_alle), None)

    def ergaenzen(self, k: Kontakt) -> Kontakt | None:
        if not k.schluessel:
            return None
        alt = self._finden(k)
        if alt is None:
            self.bestand.append(self.db.kontakt_speichern(k))
            self.neu += 1
            return k
        geaendert = False
        # Eine Anschrift nur als Ganzes übernehmen, damit keine Mischung aus zwei Adressen entsteht
        felder = [f for f in FUELLBAR if f not in ANSCHRIFT]
        if not any(getattr(alt, f) for f in ANSCHRIFT[:3]) and any(getattr(k, f) for f in ANSCHRIFT[:3]):
            felder += list(ANSCHRIFT)
        elif not alt.land and k.land:
            felder.append("land")
        for f in felder:
            if not getattr(alt, f) and getattr(k, f):
                setattr(alt, f, getattr(k, f))
                geaendert = True
        rolle = _rolle(alt.rolle, k.rolle)
        quellen = list(dict.fromkeys(alt.quellen + k.quellen))
        if rolle != alt.rolle or quellen != alt.quellen:
            alt.rolle, alt.quellen, geaendert = rolle, quellen, True
        if geaendert:
            self.db.kontakt_speichern(alt)
            self.ergaenzt += 1
        return alt


def aus_beleg(b: Beleg) -> Kontakt:
    ustid = b.partner_ustid.replace(" ", "").upper()
    a = b.anschrift
    land = a.get("land", "") or (ustid[:2] if ustid[:2].isalpha() else "")
    return Kontakt(
        name=b.partner.strip(), rolle="kunde" if b.art == Art.AUSGANG else "lieferant", ustid=ustid,
        strasse=a.get("strasse", ""), plz=a.get("plz", ""), ort=a.get("ort", ""), land="GR" if land == "EL" else land,
        email=a.get("email", ""), quellen=["E-Rechnung" if a else "Belege"],
    )


def aus_belegen(db: Datenbank, belege: list[Beleg] | None = None) -> Abgleich:
    """Legt für jeden Partner aus den Belegen einen Kontakt an bzw. ergänzt ihn (E-Rechnungen mit Anschrift)."""
    abgleich = Abgleich(db)
    for b in db.alle_belege() if belege is None else belege:
        if b.partner.strip():
            abgleich.ergaenzen(aus_beleg(b))
    return abgleich


def anschriften_uebernehmen(db: Datenbank, belege: list[Beleg]) -> None:
    """Beim Einlesen: Anschriften aus E-Rechnungen ins Adressbuch."""
    mit_anschrift = [aus_beleg(b) for b in belege if b.anschrift and b.partner.strip()]
    if mit_anschrift:
        uebernehmen(db, mit_anschrift[:1])  # alle Belege einer Rechnung haben dieselbe Anschrift


def uebernehmen(db: Datenbank, kontakte: list[Kontakt]) -> Abgleich:
    abgleich = Abgleich(db)
    for k in kontakte:
        abgleich.ergaenzen(k)
    return abgleich


def liste(db: Datenbank, ausgeblendete: bool = False) -> list[dict]:
    """Alle Kontakte mit Anzahl und Datum der Belege, nach Name sortiert."""
    aus_belegen(db)
    belege = db.alle_belege()
    ergebnis = []
    for k in db.kontakte():
        if k.ausgeblendet and not ausgeblendete:
            continue
        ustid = k.ustid.replace(" ", "").upper()
        namen = k.schluessel_alle
        eigene = [b for b in belege if schluessel(b.partner) in namen
                  or (ustid and b.partner_ustid.replace(" ", "").upper() == ustid)]
        daten = [b.rechnungsdatum for b in eigene if b.rechnungsdatum]
        ergebnis.append({
            **k.to_dict(),
            "belege": len(eigene),
            "ausgang": sum(1 for b in eigene if b.art == Art.AUSGANG),
            "eingang": sum(1 for b in eigene if b.art == Art.EINGANG),
            "letzter_beleg": max(daten).isoformat() if daten else None,
        })
    return sorted(ergebnis, key=lambda z: z["name"].lower())


def zusammenfuehren(db: Datenbank, ziel_id: int, quelle_id: int) -> Kontakt:
    """Führt ``quelle`` in ``ziel`` zusammen: leere Felder ergänzen, Name als weitere Schreibweise merken."""
    ziel, quelle = db.kontakt(ziel_id), db.kontakt(quelle_id)
    if ziel is None or quelle is None or ziel_id == quelle_id:
        raise ValueError("Zwei verschiedene, vorhandene Kontakte angeben")
    for f in (*FUELLBAR, "notiz"):
        if not getattr(ziel, f) and getattr(quelle, f):
            setattr(ziel, f, getattr(quelle, f))
    ziel.rolle = _rolle(ziel.rolle, quelle.rolle)
    ziel.quellen = list(dict.fromkeys(ziel.quellen + quelle.quellen))
    ziel.aliase = [a for a in dict.fromkeys([*ziel.aliase, quelle.name, *quelle.aliase]) if a != ziel.name]
    db.kontakt_speichern(ziel)
    db.kontakt_loeschen(quelle_id)
    return ziel


def csv_text(db: Datenbank) -> str:
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";")
    spalten = ["name", "rolle", "kundennummer", "ustid", "strasse", "plz", "ort", "land", "email", "telefon",
               "ansprechpartner", "notiz", "belege", "letzter_beleg"]
    w.writerow(spalten)
    for z in liste(db):
        w.writerow([z[s] if z[s] is not None else "" for s in spalten])
    return puffer.getvalue()
