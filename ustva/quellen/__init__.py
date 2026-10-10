"""Einlesen von Belegen aus einem Verzeichnis oder aus Paperless-ngx."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..extraktion import Erkennung
from ..models import Beleg
from ..steuerfaelle import Art, Steuerfall

REVERSE_CHARGE_EINGANG = {Steuerfall.EU_LEISTUNG_13B, Steuerfall.AUSLAND_13B, Steuerfall.EU_ERWERB}
OHNE_STEUER_AUSGANG = {
    Steuerfall.EU_LEISTUNG, Steuerfall.EU_LIEFERUNG, Steuerfall.DRITTLAND_LEISTUNG, Steuerfall.AUSFUHR,
}


@dataclass
class ImportErgebnis:
    neu: int = 0
    vorhanden: int = 0
    uebersprungen: int = 0
    fehler: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"neu": self.neu, "vorhanden": self.vorhanden, "uebersprungen": self.uebersprungen,
                "fehler": self.fehler}


def beleg_aus_erkennung(e: Erkennung, art: Art, **felder) -> Beleg:
    """Baut aus einem Erkennungsergebnis einen zu prüfenden Beleg."""
    steuerfall = e.steuerfall or (Steuerfall.INLAND if art == Art.AUSGANG else Steuerfall.VORSTEUER_INLAND)
    hinweise = list(e.hinweise)
    steuersatz = e.steuersatz
    steuer = e.steuer

    if steuerfall in REVERSE_CHARGE_EINGANG:
        # Auf der Rechnung steht keine deutsche Steuer; die Steuer berechnet man selbst.
        if not steuersatz:
            steuersatz = Decimal(19)
            hinweise.append("Reverse Charge: Steuersatz 19 % angenommen.")
        steuer = None
    elif steuerfall in OHNE_STEUER_AUSGANG or steuerfall == Steuerfall.OHNE_VORSTEUER:
        steuersatz = Decimal(0)
        steuer = Decimal(0)
    elif steuersatz is None:
        steuersatz = Decimal(19)
        hinweise.append("Kein Steuersatz erkannt, 19 % angenommen.")

    b = Beleg(
        art=art,
        steuerfall=steuerfall,
        rechnungsdatum=e.rechnungsdatum,
        netto=e.netto if e.netto is not None else Decimal(0),
        steuersatz=steuersatz,
        steuer=steuer,
        waehrung=e.waehrung,
        partner_ustid=e.partner_ustid,
        rechnungsnummer=e.rechnungsnummer,
        pruefen=True,
        hinweise=hinweise,
    )
    for name, wert in felder.items():
        setattr(b, name, wert)
    return b
