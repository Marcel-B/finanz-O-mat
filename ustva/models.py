from __future__ import annotations

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
    pruefen: bool = False  # automatisch erkannt, noch nicht bestätigt
    hinweise: list[str] = field(default_factory=list)
    id: int | None = None

    def steuer_in_belegwaehrung(self) -> Decimal:
        if self.steuer is not None:
            return self.steuer
        return self.netto * self.steuersatz / Decimal(100)

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

