"""Einlesen von Belegen aus einem Verzeichnis oder aus Paperless-ngx."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..adressbuch import anschriften_uebernehmen
from ..db import Datenbank
from ..doppelt import beschreibung, doppel, herkunft
from ..extraktion import Erkennung, steuer_passt
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
    doppelt: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"neu": self.neu, "vorhanden": self.vorhanden, "uebersprungen": self.uebersprungen,
                "fehler": self.fehler, "doppelt": self.doppelt}


def speichern(db: Datenbank, belege: list[Beleg], vorhandene: list[Beleg], ergebnis: ImportErgebnis,
              name: str) -> None:
    """Speichert die Belege eines Dokuments, außer es ist eine schon erfasste Rechnung."""
    if belege and (alt := doppel(belege[0], vorhandene)):
        ergebnis.doppelt.append(f"{name} nicht übernommen: dieselbe Rechnung wie {beschreibung(alt)} "
                                f"({herkunft(alt)}). Ist es ein Duplikat, kann es dort gelöscht werden.")
        return
    for beleg in belege:
        vorhandene.append(db.beleg_anlegen(beleg))
    anschriften_uebernehmen(db, belege)
    ergebnis.neu += 1


def _euro(wert: Decimal) -> str:
    return f"{wert:.2f} €".replace(".", ",")


def beleg_aus_erkennung(e: Erkennung, art: Art, **felder) -> Beleg:
    """Baut aus einem Erkennungsergebnis einen zu prüfenden Beleg."""
    steuerfall = e.steuerfall or (Steuerfall.INLAND if art == Art.AUSGANG else Steuerfall.VORSTEUER_INLAND)
    hinweise = list(e.hinweise)
    steuersatz = e.steuersatz
    steuer = e.steuer

    if steuerfall in REVERSE_CHARGE_EINGANG:
        # Die Steuer berechnet man selbst. Hat der Anbieter trotzdem Steuer berechnet, bleibt sie als
        # mitbezahlter Betrag stehen (für den Kontoabgleich), zählt aber nicht als Vorsteuer.
        if not steuersatz:
            steuersatz = Decimal(19)
            hinweise.append("Reverse Charge: Steuersatz 19 % angenommen.")
        steuer = steuer if steuer and steuer > 0 else None
    elif steuerfall in OHNE_STEUER_AUSGANG or steuerfall == Steuerfall.OHNE_VORSTEUER:
        steuersatz = Decimal(0)
        steuer = Decimal(0)
    elif steuersatz is None:
        steuersatz = Decimal(19)
        hinweise.append("Kein Steuersatz erkannt, 19 % angenommen.")

    pruefbar = steuerfall not in REVERSE_CHARGE_EINGANG  # dort ist es fremde Steuer, kein Rechenwert
    if pruefbar and steuer is not None and e.netto and not steuer_passt(e.netto, steuer, steuersatz):
        text = f"Erkannte Steuer {_euro(steuer)} passt nicht zu {steuersatz:g} % von {_euro(e.netto)}"
        if Decimal(0) < steuer / e.netto <= Decimal("0.1905"):
            # Kann eine Rechnung mit mehreren Steuersätzen sein; dann stimmt der Betrag.
            hinweise.append(f"{text}. Mehrere Steuersätze? Bitte mit der Rechnung vergleichen.")
        else:
            # Negative oder zu hohe Steuer stammt aus der falschen Zeile; dann lieber dem Satz glauben.
            hinweise.append(f"{text}; aus dem Steuersatz berechnet. Bitte mit der Rechnung vergleichen.")
            steuer = None

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
