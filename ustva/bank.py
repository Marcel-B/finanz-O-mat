"""Kontoumsätze (N26-CSV) einlesen und mit den Belegen abgleichen.

N26 bietet den Export unter „Kontoauszüge“ bzw. „Umsätze herunterladen“ als CSV an.
Unterstützt werden das aktuelle englische Format (Booking Date, Partner Name, …),
das ältere (Date, Payee, …) und die deutsche Variante (Datum, Empfänger, …).

Der Abgleich ordnet jedem Umsatz höchstens einen Beleg zu: gleiche Richtung
(Einnahme ↔ Ausgangsrechnung, Ausgabe ↔ Eingangsrechnung), passender Betrag und
ein Buchungsdatum kurz vor bis einige Monate nach dem Rechnungsdatum. Bei
Ausgangsrechnungen übernimmt er das Buchungsdatum als Zahlungsdatum, was bei der
Ist-Versteuerung über den Voranmeldungszeitraum entscheidet.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .config import Config
from .db import Datenbank
from .extraktion import betrag_parsen
from .models import Beleg, Umsatz
from .steuerfaelle import Art, Steuerfall

SPALTEN = {
    "datum": ["booking date", "date", "datum", "buchungsdatum"],
    "partner": ["partner name", "payee", "empfänger", "zahlungsempfänger", "auftraggeber/empfänger"],
    "iban": ["partner iban", "account number", "kontonummer", "iban"],
    "typ": ["type", "transaction type", "transaktionstyp", "typ"],
    "zweck": ["payment reference", "verwendungszweck", "reference"],
    "betrag": ["amount (eur)", "betrag (eur)", "amount", "betrag"],
    "original_betrag": ["original amount", "amount (foreign currency)", "betrag (fremdwährung)"],
    "original_waehrung": ["original currency", "type foreign currency", "fremdwährung"],
}

KONTO_ORDNER = ["konto", "kontoauszuege", "kontoauszüge", "bank"]
TOLERANZ = Decimal("0.01")
VOR_RECHNUNG = timedelta(days=10)  # Vorkasse
NACH_RECHNUNG = timedelta(days=120)


class CsvFormatFehler(ValueError):
    pass


def _datum(text: str) -> date:
    text = text.strip()
    if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text):
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return date.fromisoformat(text[:10])


def n26_csv_lesen(text: str, konto: str = "N26") -> list[Umsatz]:
    text = text.lstrip("﻿")
    if not text.strip():
        return []
    try:
        dialekt = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialekt = csv.excel
    zeilen = list(csv.reader(io.StringIO(text), dialekt))
    kopf = [k.strip().strip('"').lower() for k in zeilen[0]]
    index: dict[str, int] = {}
    for feld, namen in SPALTEN.items():
        for name in namen:
            if name in kopf:
                index[feld] = kopf.index(name)
                break
    if "datum" not in index or "betrag" not in index:
        raise CsvFormatFehler(
            "Unbekanntes CSV-Format: Spalten für Datum und Betrag fehlen. Bitte den CSV-Export von N26 verwenden."
        )

    umsaetze: list[Umsatz] = []
    gesehen: Counter[str] = Counter()
    for nr, zeile in enumerate(zeilen[1:], start=2):
        if not any(z.strip() for z in zeile):
            continue

        def wert(feld: str, zeile=zeile) -> str:
            i = index.get(feld)
            return zeile[i].strip() if i is not None and i < len(zeile) else ""

        try:
            buchungsdatum = _datum(wert("datum"))
        except ValueError as e:
            raise CsvFormatFehler(f"Zeile {nr}: Datum „{wert('datum')}“ nicht lesbar") from e
        betrag = betrag_parsen(wert("betrag"))
        if betrag is None:
            raise CsvFormatFehler(f"Zeile {nr}: Betrag „{wert('betrag')}“ nicht lesbar")
        original = betrag_parsen(wert("original_betrag")) if wert("original_betrag") else None
        u = Umsatz(
            konto=konto,
            buchungsdatum=buchungsdatum,
            betrag=betrag,
            partner=wert("partner"),
            iban=wert("iban").replace(" ", ""),
            verwendungszweck=wert("zweck"),
            typ=wert("typ"),
            original_betrag=original,
            original_waehrung=wert("original_waehrung").upper(),
        )
        # Gleiche Umsätze am selben Tag (zwei gleiche Kartenzahlungen) bleiben über die laufende Nummer getrennt.
        schluessel = "|".join(str(x) for x in (
            konto, u.buchungsdatum, u.betrag, u.partner, u.iban, u.verwendungszweck, u.typ, u.original_betrag,
        ))
        gesehen[schluessel] += 1
        u.pruefsumme = hashlib.sha256(f"{schluessel}|{gesehen[schluessel]}".encode()).hexdigest()
        umsaetze.append(u)
    return umsaetze


@dataclass
class BankImport:
    neu: int = 0
    vorhanden: int = 0
    zugeordnet: int = 0
    fehler: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"neu": self.neu, "vorhanden": self.vorhanden, "zugeordnet": self.zugeordnet, "fehler": self.fehler}


def ignorier_grund(u: Umsatz, regeln: dict[str, str], stichworte: list[str]) -> str:
    partner = u.partner.strip().lower()
    if partner in regeln:
        return regeln[partner]
    text = f"{u.partner} {u.verwendungszweck}".lower()
    for wort in stichworte:
        if wort and wort.lower() in text:
            return f"Stichwort „{wort}“"
    return ""


def importieren(db: Datenbank, cfg: Config, text: str) -> BankImport:
    ergebnis = BankImport()
    try:
        umsaetze = n26_csv_lesen(text)
    except CsvFormatFehler as e:
        ergebnis.fehler.append(str(e))
        return ergebnis
    regeln = db.bank_regeln()
    for u in umsaetze:
        if grund := ignorier_grund(u, regeln, cfg.bank_ignorieren):
            u.ignoriert, u.ignoriert_grund = True, grund
        if db.umsatz_anlegen(u):
            ergebnis.neu += 1
        else:
            ergebnis.vorhanden += 1
    ergebnis.zugeordnet = abgleich_ausfuehren(db)
    return ergebnis


def verzeichnis_importieren(db: Datenbank, cfg: Config) -> BankImport | None:
    """Liest CSV-Dateien aus dem Unterordner konto/ des Rechnungsverzeichnisses."""
    if not cfg.rechnungs_verzeichnis or not cfg.rechnungs_verzeichnis.is_dir():
        return None
    dateien = sorted(
        p for p in cfg.rechnungs_verzeichnis.rglob("*.csv")
        if any(t.lower() in KONTO_ORDNER for t in p.relative_to(cfg.rechnungs_verzeichnis).parts[:-1])
    )
    if not dateien:
        return None
    gesamt = BankImport()
    for pfad in dateien:
        e = importieren(db, cfg, pfad.read_text(encoding="utf-8-sig", errors="replace"))
        gesamt.neu += e.neu
        gesamt.vorhanden += e.vorhanden
        gesamt.zugeordnet += e.zugeordnet
        gesamt.fehler += [f"{pfad.name}: {f}" for f in e.fehler]
    return gesamt


def passt_betrag(u: Umsatz, b: Beleg) -> bool:
    soll = abs(b.zahlbetrag())
    if b.waehrung.upper() == "EUR":
        return abs(abs(u.betrag) - soll) <= TOLERANZ
    if u.original_betrag is not None and u.original_waehrung == b.waehrung.upper():
        return abs(abs(u.original_betrag) - soll) <= TOLERANZ
    return False


def _woerter(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", text.lower()) if w not in {"gmbh", "inc", "llc", "ltd", "the"}}


def kandidaten(u: Umsatz, belege: list[Beleg], vergeben: set[int], nur_passende: bool = True) -> list[Beleg]:
    """Mögliche Belege für einen Umsatz, der beste zuerst."""
    art = Art.AUSGANG if u.betrag > 0 else Art.EINGANG
    treffer: list[tuple[tuple, Beleg]] = []
    for b in belege:
        if b.id in vergeben or b.art != art or b.steuerfall == Steuerfall.IGNORIEREN or b.rechnungsdatum is None:
            continue
        im_fenster = b.rechnungsdatum - VOR_RECHNUNG <= u.buchungsdatum <= b.rechnungsdatum + NACH_RECHNUNG
        betrag_ok = passt_betrag(u, b)
        if nur_passende and not (im_fenster and betrag_ok):
            continue
        nummer = bool(b.rechnungsnummer) and len(b.rechnungsnummer) >= 4 and \
            b.rechnungsnummer.lower() in u.verwendungszweck.lower()
        name = bool(_woerter(b.partner) & _woerter(f"{u.partner} {u.verwendungszweck}"))
        abstand = abs((u.buchungsdatum - b.rechnungsdatum).days)
        treffer.append(((not betrag_ok, not im_fenster, not nummer, not name, abstand), b))
    return [b for _, b in sorted(treffer, key=lambda t: t[0])]


def zuordnen(db: Datenbank, u: Umsatz, b: Beleg | None) -> None:
    u.beleg_id = b.id if b else None
    if b is not None:
        u.ignoriert, u.ignoriert_grund = False, ""
        if b.art == Art.AUSGANG and b.zahlungsdatum is None:
            b.zahlungsdatum = u.buchungsdatum
            b.hinweise = [*b.hinweise, f"Zahlungsdatum aus Kontoumsatz vom {u.buchungsdatum:%d.%m.%Y} übernommen."]
            db.beleg_aktualisieren(b)
    db.umsatz_aktualisieren(u)


def abgleich_ausfuehren(db: Datenbank) -> int:
    """Ordnet offenen Umsätzen automatisch passende Belege zu. Gibt die Anzahl neuer Zuordnungen zurück."""
    belege = db.alle_belege()
    umsaetze = db.alle_umsaetze()
    vergeben = {u.beleg_id for u in umsaetze if u.beleg_id is not None}
    neu = 0
    for u in umsaetze:
        if u.beleg_id is not None or u.ignoriert:
            continue
        passend = kandidaten(u, belege, vergeben)
        if passend:
            zuordnen(db, u, passend[0])
            vergeben.add(passend[0].id)
            neu += 1
    return neu


def ohne_beleg(umsaetze: list[Umsatz], zeitraum: str) -> list[Umsatz]:
    return [u for u in umsaetze if u.zeitraum == zeitraum and u.beleg_id is None and not u.ignoriert]


def _euro(x: Decimal) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def hinweis_fehlende_belege(umsaetze: list[Umsatz], zeitraum: str) -> str | None:
    fehlend = ohne_beleg(umsaetze, zeitraum)
    if not fehlend:
        return None
    ausgaben = sum((u.betrag for u in fehlend if u.betrag < 0), Decimal(0))
    einnahmen = sum((u.betrag for u in fehlend if u.betrag > 0), Decimal(0))
    teile = []
    if einnahmen:
        teile.append(f"Einnahmen {_euro(einnahmen)} €")
    if ausgaben:
        teile.append(f"Ausgaben {_euro(-ausgaben)} €")
    return (
        f"{len(fehlend)} Kontoumsatz/-umsätze ohne Beleg ({', '.join(teile)}). Rechnung einlesen und zuordnen, "
        "oder unter Kontoumsätze als „kein Beleg nötig“ markieren (z. B. privat, Steuerzahlung, Umbuchung)."
    )
