from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from .steuerfaelle import Art, Steuerfall


@dataclass
class Beleg:
    """Eine Ein- oder Ausgangsrechnung.

    Beträge stehen in der Belegwährung. ``kurs`` ist der Umrechnungskurs in
    Einheiten der Fremdwährung je 1 EUR (wie bei EZB und BMF üblich); fehlt er,
    wird der Monatskurs aus der Kurstabelle bzw. der EZB verwendet.
    """

    art: Art
    steuerfall: Steuerfall
    rechnungsdatum: date | None
    netto: Decimal
    steuersatz: Decimal = Decimal("19")
    steuer: Decimal | None = None  # ausgewiesene Steuer; None -> netto * steuersatz
    waehrung: str = "EUR"
    kurs: Decimal | None = None
    zahlungsdatum: date | None = None
    partner: str = ""
    partner_ustid: str = ""
    rechnungsnummer: str = ""
    beschreibung: str = ""
    quelle: str = "manuell"  # manuell | verzeichnis | paperless
    quelle_ref: str = ""  # Pfad oder Paperless-Dokument-ID
    quelle_link: str = ""
    erechnung: str = ""  # Format, wenn aus einer E-Rechnung gelesen (z. B. "XRechnung (UBL)")
    pruefen: bool = False  # automatisch erkannt, noch nicht bestätigt
    hinweise: list[str] = field(default_factory=list)
    id: int | None = None
    # Anschrift des Partners aus einer E-Rechnung (strasse, plz, ort, land, email); nur beim Einlesen gefüllt,
    # landet im Adressbuch, nicht am Beleg
    anschrift: dict[str, str] = field(default_factory=dict)

    def steuer_in_belegwaehrung(self) -> Decimal:
        if self.steuer is not None:
            return self.steuer
        return self.netto * self.steuersatz / Decimal(100)

    def steuer_auf_rechnung(self) -> bool:
        """Bei Reverse Charge und steuerfreien Umsätzen steht keine Steuer auf der Rechnung."""
        return self.steuerfall in (Steuerfall.INLAND, Steuerfall.VORSTEUER_INLAND)

    def zahlbetrag(self) -> Decimal:
        """Betrag, der laut Rechnung bezahlt wird (in Belegwährung)."""
        if self.steuer_auf_rechnung():
            return self.netto + self.steuer_in_belegwaehrung()
        # Reverse Charge: nur Steuer, die der Anbieter trotzdem berechnet hat
        return self.netto + (self.steuer or Decimal(0))

    def to_dict(self) -> dict:
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, Decimal):
                d[key] = str(value)
            elif isinstance(value, date):
                d[key] = value.isoformat()
            elif isinstance(value, Enum):
                d[key] = value.value
        return d



@dataclass
class Umsatz:
    """Eine Buchung vom Bankkonto. ``betrag`` in EUR, negativ für Ausgaben."""

    buchungsdatum: date
    betrag: Decimal
    partner: str = ""
    iban: str = ""
    verwendungszweck: str = ""
    typ: str = ""
    original_betrag: Decimal | None = None
    original_waehrung: str = ""
    konto: str = "N26"
    pruefsumme: str = ""
    beleg_id: int | None = None
    ignoriert: bool = False
    ignoriert_grund: str = ""
    id: int | None = None

    @property
    def zeitraum(self) -> str:
        return f"{self.buchungsdatum:%Y-%m}"

    def to_dict(self) -> dict:
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, Decimal):
                d[key] = str(value)
            elif isinstance(value, date):
                d[key] = value.isoformat()
        return d


def schluessel(name: str) -> str:
    """Name zum Wiedererkennen: ohne Groß-/Kleinschreibung und Satzzeichen."""
    return re.sub(r"[^0-9a-zäöüß]+", " ", name.lower()).strip()


@dataclass
class Kontakt:
    """Eintrag im Adressbuch (siehe ustva/adressbuch.py)."""

    name: str
    rolle: str = ""  # kunde | lieferant | beides | ""
    kundennummer: str = ""
    ustid: str = ""
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = ""  # ISO-Code, wenn bekannt
    email: str = ""
    telefon: str = ""
    ansprechpartner: str = ""
    notiz: str = ""
    quellen: list[str] = field(default_factory=list)
    aliase: list[str] = field(default_factory=list)  # weitere Schreibweisen (nach dem Zusammenführen)
    ausgeblendet: bool = False
    id: int | None = None

    @property
    def schluessel(self) -> str:
        return schluessel(self.name)

    @property
    def schluessel_alle(self) -> set[str]:
        return {schluessel(n) for n in [self.name, *self.aliase]} - {""}

    def to_dict(self) -> dict:
        return asdict(self)
