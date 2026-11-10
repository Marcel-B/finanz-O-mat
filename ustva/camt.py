"""Kontoauszüge im Format CAMT.053 einlesen (z. B. aus MoneyMoney: Ablage → Exportieren → CAMT.053).

Die Umsätze landen in der Kontenübersicht (Reiter „Konten“) beim verbundenen Konto mit derselben IBAN, nicht in der
Buchhaltung. Wird ein Konto später für die Buchhaltung eingeschaltet, übernimmt die App sie wie die abgerufenen.

Umsätze, die schon über die Schnittstelle kamen, werden nicht doppelt angelegt (gleiches Konto, gleicher Betrag,
Buchungstag bis drei Tage auseinander). Umgekehrt übernimmt ein späterer Abruf einen eingelesenen Umsatz, statt ihn
ein zweites Mal anzulegen. Dieselbe Datei zweimal einzulesen legt nichts doppelt an.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .db import Datenbank
from .kontoabruf import CSV_TAGE, IMPORT_PRAEFIX, SEPA_PRAEFIX
from .models import Umsatz


class CamtFehler(ValueError):
    pass


@dataclass
class Auszug:
    """Ein Kontoauszug (``Stmt``) der Datei."""

    iban: str
    waehrung: str = "EUR"
    umsaetze: list[Umsatz] = field(default_factory=list)
    vorgemerkt: int = 0


@dataclass
class Einlesen:
    iban: str
    konto: str = ""  # Name in der Übersicht, leer, wenn kein Konto passt
    neu: int = 0
    vorhanden: int = 0  # schon eingelesen oder schon abgerufen
    vorgemerkt: int = 0
    von: date | None = None
    bis: date | None = None
    fehler: str = ""

    def to_dict(self) -> dict:
        return {"iban": self.iban, "konto": self.konto, "neu": self.neu, "vorhanden": self.vorhanden,
                "vorgemerkt": self.vorgemerkt, "von": self.von.isoformat() if self.von else None,
                "bis": self.bis.isoformat() if self.bis else None, "fehler": self.fehler}


def _name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _kind(element: ET.Element | None, *pfad: str) -> ET.Element | None:
    """Kindelement über lokale Namen, unabhängig von der Version des Namensraums (camt.053.001.02 bis .13)."""
    for teil in pfad:
        if element is None:
            return None
        element = next((k for k in element if _name(k) == teil), None)
    return element


def _kinder(element: ET.Element | None, name: str) -> list[ET.Element]:
    return [k for k in element if _name(k) == name] if element is not None else []


def _text(element: ET.Element | None, *pfad: str) -> str:
    e = _kind(element, *pfad)
    return (e.text or "").strip() if e is not None else ""


def _datum(element: ET.Element | None) -> date | None:
    for feld in ("Dt", "DtTm"):
        if t := _text(element, feld):
            try:
                return date.fromisoformat(t[:10])
            except ValueError:
                return None
    return None


def _betrag(element: ET.Element | None) -> tuple[Decimal, str] | None:
    if element is None or not (element.text or "").strip():
        return None
    try:
        return Decimal(element.text.strip()), (element.get("Ccy") or "EUR").upper()
    except InvalidOperation:
        return None


def _partei(details: ET.Element | None, rolle: str) -> tuple[str, str]:
    """Name und IBAN von Zahler (Dbtr) oder Empfänger (Cdtr); ab camt.053.001.08 unter Pty."""
    parteien = _kind(details, "RltdPties")
    name = _text(parteien, rolle, "Nm") or _text(parteien, rolle, "Pty", "Nm")
    # Abweichender Zahler/Empfänger (Ultmt…) nur, wenn sonst niemand genannt ist
    name = name or _text(parteien, f"Ultmt{rolle}", "Nm") or _text(parteien, f"Ultmt{rolle}", "Pty", "Nm")
    iban = _text(parteien, f"{rolle}Acct", "Id", "IBAN")
    return name, iban.replace(" ", "").upper()


def _umsatz(eintrag: ET.Element, waehrung: str) -> Umsatz | None:
    status = _text(eintrag, "Sts") or _text(eintrag, "Sts", "Cd")
    if status and status.upper() not in ("BOOK", "BOOKED"):
        return None  # vorgemerkt
    menge = _betrag(_kind(eintrag, "Amt"))
    buchungstag = _datum(_kind(eintrag, "BookgDt")) or _datum(_kind(eintrag, "ValDt"))
    if menge is None or buchungstag is None:
        return None
    betrag, ccy = menge
    eingang = _text(eintrag, "CdtDbtInd") == "CRDT"
    betrag = abs(betrag) if eingang else -abs(betrag)
    details = _kind(eintrag, "NtryDtls", "TxDtls")
    partner, iban = _partei(details, "Dbtr" if eingang else "Cdtr")
    zweck = " ".join(t for t in (SEPA_PRAEFIX.sub("", (z.text or "")).strip()
                                 for z in _kinder(_kind(details, "RmtInf"), "Ustrd")) if t)
    zweck = re.sub(r"\s+", " ", zweck)
    zusatz = _text(eintrag, "AddtlNtryInf") or _text(details, "AddtlTxInf")
    if not zweck and zusatz:
        zweck = zusatz
    typ = (_text(eintrag, "BkTxCd", "Prtry", "Cd") or _text(details, "BkTxCd", "Prtry", "Cd")
           or (zusatz if zusatz != zweck else ""))
    original, original_waehrung = None, ""
    instruiert = _betrag(_kind(details, "AmtDtls", "InstdAmt", "Amt"))
    if instruiert and instruiert[1] != waehrung:
        original, original_waehrung = (abs(instruiert[0]) if eingang else -abs(instruiert[0])), instruiert[1]
    elif ccy != waehrung:
        original, original_waehrung = betrag, ccy
    u = Umsatz(buchungsdatum=buchungstag, betrag=betrag, partner=(partner or zweck[:80]).strip(), iban=iban,
               verwendungszweck=zweck, typ=typ[:80], original_betrag=original, original_waehrung=original_waehrung)
    u.pruefsumme = _text(eintrag, "AcctSvcrRef") or _text(details, "Refs", "AcctSvcrRef")  # vorläufig: Kennung
    return u


def lesen(inhalt: bytes) -> list[Auszug]:
    """Alle Auszüge einer CAMT.053-Datei; vorgemerkte Umsätze werden nur gezählt."""
    try:
        wurzel = ET.fromstring(inhalt)
    except ET.ParseError as e:
        raise CamtFehler(f"Keine lesbare XML-Datei: {e}") from e
    if "camt.053" not in wurzel.tag and _kind(wurzel, "BkToCstmrStmt") is None:
        raise CamtFehler("Keine CAMT.053-Datei (Kontoauszug). MT940 und CSV liest dieser Import nicht.")
    auszuege: dict[str, Auszug] = {}
    for stmt in _kinder(_kind(wurzel, "BkToCstmrStmt"), "Stmt"):
        iban = _text(stmt, "Acct", "Id", "IBAN").replace(" ", "").upper()
        if not iban:
            raise CamtFehler("Ein Auszug der Datei hat keine IBAN.")
        a = auszuege.setdefault(iban, Auszug(iban, _text(stmt, "Acct", "Ccy").upper() or "EUR"))
        for eintrag in _kinder(stmt, "Ntry"):
            if (u := _umsatz(eintrag, a.waehrung)) is None:
                a.vorgemerkt += 1
            else:
                a.umsaetze.append(u)
    if not auszuege:
        raise CamtFehler("Die Datei enthält keinen Kontoauszug.")
    return list(auszuege.values())


def pruefsummen(schluessel: str, umsaetze: list[Umsatz]) -> None:
    """Wie beim Abruf: aus der Kennung der Bank, sonst aus dem Inhalt mit laufender Nummer je gleichem Umsatz.

    So ergibt eine zweite Datei, die sich mit der ersten überschneidet, für dieselben Umsätze dieselben Prüfsummen,
    solange sie ganze Tage enthält.
    """
    gesehen: Counter[str] = Counter()
    for u in sorted(umsaetze, key=lambda u: u.buchungsdatum):
        if u.pruefsumme:
            inhalt = f"{schluessel}|camt|{u.pruefsumme}"
        else:
            inhalt = "|".join(str(x) for x in (schluessel, u.buchungsdatum, u.betrag, u.partner, u.verwendungszweck))
            gesehen[inhalt] += 1
            inhalt += f"|{gesehen[inhalt]}"
        u.pruefsumme = IMPORT_PRAEFIX + hashlib.sha256(inhalt.encode()).hexdigest()


def einlesen(db: Datenbank, inhalt: bytes, trocken: bool = False) -> list[Einlesen]:
    """Liest eine CAMT.053-Datei in die Kontenübersicht der verbundenen Konten mit derselben IBAN."""
    konten = {k.iban: k for k in db.bank_konten() if k.iban}
    ergebnisse = []
    for a in lesen(inhalt):
        e = Einlesen(a.iban, vorgemerkt=a.vorgemerkt)
        ergebnisse.append(e)
        if a.umsaetze:
            e.von = min(u.buchungsdatum for u in a.umsaetze)
            e.bis = max(u.buchungsdatum for u in a.umsaetze)
        if (k := konten.get(a.iban)) is None:
            e.fehler = "kein verbundenes Konto mit dieser IBAN (erst mit bank verbinden anlegen)"
            continue
        e.konto = k.bezeichnung or k.bank
        pruefsummen(k.schluessel, a.umsaetze)
        neu = []
        vergeben: set[str] = set()
        for u in a.umsaetze:
            if db.konto_buchung_vorhanden(u.pruefsumme) or _schon_abgerufen(db, k.schluessel, u, vergeben):
                e.vorhanden += 1
            else:
                neu.append(u)
        e.neu = len(neu) if trocken else db.konto_buchungen_speichern(k.schluessel, neu)
    return ergebnisse


def _schon_abgerufen(db: Datenbank, schluessel: str, u: Umsatz, vergeben: set[str]) -> bool:
    """Steht in der Übersicht ein abgerufener, noch nicht vergebener Umsatz gleichen Betrags bis drei Tage daneben?"""
    tage = timedelta(days=CSV_TAGE)
    kandidaten = [z for z in db.konto_buchungen_von_bis(schluessel, u.buchungsdatum - tage, u.buchungsdatum + tage)
                  if not z["pruefsumme"].startswith(IMPORT_PRAEFIX) and z["pruefsumme"] not in vergeben
                  and Decimal(z["betrag"]) == u.betrag]
    if not kandidaten:
        return False
    naechster = min(kandidaten, key=lambda z: abs((date.fromisoformat(z["buchungsdatum"]) - u.buchungsdatum).days))
    vergeben.add(naechster["pruefsumme"])
    return True
