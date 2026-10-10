"""Kontoumsätze (N26-CSV) einlesen und mit den Belegen abgleichen.

N26 bietet den Export unter „Kontoauszüge“ bzw. „Umsätze herunterladen“ als CSV an.
Unterstützt werden das aktuelle englische Format (Booking Date, Partner Name, …),
das ältere (Date, Payee, …) und die deutsche Variante (Datum, Empfänger, …).

Der Abgleich ordnet jedem Umsatz höchstens einen Beleg zu: gleiche Richtung
(Einnahme ↔ Ausgangsrechnung, Ausgabe ↔ Eingangsrechnung), passender Betrag und
ein Buchungsdatum kurz vor bis einige Monate nach dem Rechnungsdatum. Bei
Fremdwährung ohne Originalbetrag im Export reicht ein ungefähr passender
Euro-Betrag, wenn auch der Name passt; die Differenz sind Kosten des
Geldverkehrs (Kursaufschlag, Gebühren) und für die Umsatzsteuer egal. Bei
Ausgangsrechnungen übernimmt er das Buchungsdatum als Zahlungsdatum, was bei der
Ist-Versteuerung über den Voranmeldungszeitraum entscheidet.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .config import Config
from .db import Datenbank
from .extraktion import betrag_parsen
from .models import Beleg, Umsatz
from .steuerfaelle import Art, Steuerfall
from .waehrung import KursFehlt, Umrechner

Euro = Callable[[Beleg], "Decimal | None"]  # Zahlbetrag eines Belegs in EUR, falls bekannt

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
KURS_TOLERANZ = Decimal("0.05")  # Kursaufschlag und Auslandsgebühren der Bank
VOR_RECHNUNG = timedelta(days=10)  # Vorkasse
NACH_RECHNUNG = timedelta(days=120)
# Lastschrift und Karte folgen einer Eingangsrechnung nach wenigen Tagen. Ohne Rechnungsnummer im
# Verwendungszweck ordnen wir automatisch nur in diesem Abstand zu, sonst landet bei Abos mit gleichem
# Betrag die Abbuchung schnell bei der Rechnung des Vormonats.
NACH_RECHNUNG_ABBUCHUNG = timedelta(days=20)


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


# Grund für Umsätze, die keine betriebliche Ausgabe sind (versehentlich vom Geschäftskonto bezahlt usw.).
# Sie zählen nicht zur Umsatzsteuer, die Steuerberatung bekommt sie im Monats-ZIP aufgelistet.
FEHLBUCHUNG = "Fehlbuchung/privat"


def ist_fehlbuchung(u: Umsatz) -> bool:
    return u.ignoriert and u.ignoriert_grund.startswith(FEHLBUCHUNG)


def ignorier_grund(u: Umsatz, regeln: dict[str, str], stichworte: list[str]) -> str:
    partner = u.partner.strip().lower()
    if partner in regeln:
        return regeln[partner]
    text = f"{u.partner} {u.verwendungszweck}".lower()
    for wort in stichworte:
        if wort and wort.lower() in text:
            return f"Stichwort „{wort}“"
    return ""


def importieren(db: Datenbank, cfg: Config, text: str, umrechner: Umrechner | None = None) -> BankImport:
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
    ergebnis.zugeordnet = abgleich_ausfuehren(db, umrechner)
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


def euro_schaetzer(umrechner: Umrechner) -> Euro:
    def euro(b: Beleg) -> Decimal | None:
        if b.waehrung.upper() == "EUR":
            return b.zahlbetrag()
        if b.rechnungsdatum is None:
            return None
        try:
            return umrechner.in_euro(b.zahlbetrag(), b.waehrung, b.rechnungsdatum, b.kurs)[0]
        except KursFehlt:
            return None
    return euro


def passt_betrag(u: Umsatz, b: Beleg, euro: Euro | None = None) -> str | None:
    """"genau", "ungefaehr" (Fremdwährung mit Kursdifferenz) oder None."""
    soll = abs(b.zahlbetrag())
    if b.waehrung.upper() == "EUR":
        return "genau" if abs(abs(u.betrag) - soll) <= TOLERANZ else None
    if u.original_betrag is not None and u.original_waehrung == b.waehrung.upper():
        return "genau" if abs(abs(u.original_betrag) - soll) <= TOLERANZ else None
    if euro and (geschaetzt := euro(b)):
        geschaetzt = abs(geschaetzt)
        if abs(abs(u.betrag) - geschaetzt) <= max(Decimal("0.50"), geschaetzt * KURS_TOLERANZ):
            return "ungefaehr"
    return None


def kosten_geldverkehr(u: Umsatz, b: Beleg, euro: Euro) -> Decimal | None:
    """Was die Bank mehr abgebucht (oder weniger gutgeschrieben) hat als der Beleg in EUR."""
    if (geschaetzt := euro(b)) is None:
        return None
    return abs(u.betrag) - abs(geschaetzt) if u.betrag < 0 else abs(geschaetzt) - abs(u.betrag)


def _woerter(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", text.lower()) if w not in {"gmbh", "inc", "llc", "ltd", "the"}}


def kandidaten(u: Umsatz, belege: list[Beleg], vergeben: set[int], nur_passende: bool = True,
               euro: Euro | None = None) -> list[Beleg]:
    """Mögliche Belege für einen Umsatz, der beste zuerst."""
    return [b for _, b in sorted(_bewertet(u, belege, vergeben, nur_passende, euro), key=lambda t: t[0])]


def _bewertet(u: Umsatz, belege: list[Beleg], vergeben: set[int], nur_passende: bool,
              euro: Euro | None) -> list[tuple[tuple, Beleg]]:
    art = Art.AUSGANG if u.betrag > 0 else Art.EINGANG
    treffer: list[tuple[tuple, Beleg]] = []
    for b in belege:
        if b.id in vergeben or b.art != art or b.steuerfall == Steuerfall.IGNORIEREN:
            continue
        if b.rechnungsdatum is None and nur_passende:
            continue  # zur manuellen Auswahl steht er trotzdem
        im_fenster = b.rechnungsdatum is not None and \
            b.rechnungsdatum - VOR_RECHNUNG <= u.buchungsdatum <= b.rechnungsdatum + NACH_RECHNUNG
        betrag = passt_betrag(u, b, euro)
        nummer = bool(b.rechnungsnummer) and len(b.rechnungsnummer) >= 4 and \
            b.rechnungsnummer.lower() in u.verwendungszweck.lower()
        name = bool(_woerter(f"{b.partner} {b.beschreibung}") & _woerter(f"{u.partner} {u.verwendungszweck}"))
        # Ungefähr passende Beträge nur zusammen mit dem Namen, sonst trifft es zu leicht den falschen Beleg.
        sicher = betrag == "genau" or (betrag == "ungefaehr" and name)
        nah = nummer or b.art == Art.AUSGANG or (
            b.rechnungsdatum is not None and u.buchungsdatum <= b.rechnungsdatum + NACH_RECHNUNG_ABBUCHUNG)
        if nur_passende and not (im_fenster and sicher and nah):
            continue
        abstand = abs((u.buchungsdatum - b.rechnungsdatum).days) if b.rechnungsdatum else 99999
        rang = {"genau": 0, "ungefaehr": 1}.get(betrag, 2)
        treffer.append(((rang, not im_fenster, not nummer, not name, abstand), b))
    return treffer


def passt_zur_suche(b: Beleg, suche: str) -> bool:
    """Alle Suchwörter kommen in Partner, Beschreibung, Rechnungsnummer, Datum oder Betrag vor."""
    betrag = f"{abs(b.zahlbetrag()):.2f}"
    datum = f"{b.rechnungsdatum:%d.%m.%Y} {b.rechnungsdatum.isoformat()}" if b.rechnungsdatum else ""
    text = " ".join([b.partner, b.beschreibung, b.rechnungsnummer, b.waehrung, datum, betrag,
                     betrag.replace(".", ",")]).lower()
    return all(wort in text for wort in suche.lower().split())


def zuordnen(db: Datenbank, u: Umsatz, b: Beleg | None) -> None:
    u.beleg_id = b.id if b else None
    if b is not None:
        u.ignoriert, u.ignoriert_grund = False, ""
        if b.art == Art.AUSGANG and b.zahlungsdatum is None:
            b.zahlungsdatum = u.buchungsdatum
            b.hinweise = [*b.hinweise, f"Zahlungsdatum aus Kontoumsatz vom {u.buchungsdatum:%d.%m.%Y} übernommen."]
            db.beleg_aktualisieren(b)
    db.umsatz_aktualisieren(u)


def abgleich_ausfuehren(db: Datenbank, umrechner: Umrechner | None = None) -> int:
    """Ordnet offenen Umsätzen automatisch passende Belege zu. Gibt die Anzahl neuer Zuordnungen zurück."""
    euro = euro_schaetzer(umrechner or Umrechner(db, ezb_abrufen=False))
    belege = db.alle_belege()
    umsaetze = db.alle_umsaetze()
    vergeben = {u.beleg_id for u in umsaetze if u.beleg_id is not None}
    # Alle passenden Paare, das beste zuerst. So bekommt bei gleichen Beträgen (Abos) jede Rechnung die
    # zeitlich nächste Abbuchung, egal in welcher Reihenfolge die Monate eingelesen wurden.
    paare = [(wertung, i, u, b) for i, u in enumerate(umsaetze) if u.beleg_id is None and not u.ignoriert
             for wertung, b in _bewertet(u, belege, vergeben, True, euro)]
    paare.sort(key=lambda p: (p[0], p[1]))
    neu = 0
    erledigt: set[int] = set()
    for _, _, u, b in paare:
        if u.id in erledigt or b.id in vergeben:
            continue
        zuordnen(db, u, b)
        erledigt.add(u.id)
        vergeben.add(b.id)
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
