"""Steuerfälle und ihre Zuordnung zu den Kennzahlen der UStVA (Vordruck USt 1 A).

Jeder Beleg bekommt genau einen Steuerfall. Daraus ergibt sich, in welche
Kennzahl(en) sein Nettobetrag und seine Steuer fließen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Art(StrEnum):
    AUSGANG = "ausgang"  # eigene Rechnung an Kunden (Umsatz)
    EINGANG = "eingang"  # Rechnung eines Lieferanten (Ausgabe)


class Steuerfall(StrEnum):
    # Ausgangsrechnungen
    INLAND = "inland"  # steuerpflichtig im Inland, 19 % -> Kz 81, 7 % -> Kz 86, sonst Kz 35/36
    EU_LEISTUNG = "eu_leistung"  # sonstige Leistung an Unternehmer im EU-Ausland -> Kz 21 (+ ZM)
    EU_LIEFERUNG = "eu_lieferung"  # innergemeinschaftliche Lieferung -> Kz 41 (+ ZM)
    DRITTLAND_LEISTUNG = "drittland_leistung"  # Leistungsort im Drittland -> Kz 45
    AUSFUHR = "ausfuhr"  # steuerfreie Ausfuhrlieferung -> Kz 43
    # Eingangsrechnungen
    VORSTEUER_INLAND = "vorsteuer_inland"  # Rechnung mit deutscher USt -> Kz 66
    EU_LEISTUNG_13B = "eu_leistung_13b"  # Leistung eines EU-Unternehmers, Reverse Charge -> Kz 46/47, 67
    AUSLAND_13B = "ausland_13b"  # Leistung eines Drittland-Unternehmers (z. B. OpenAI), Reverse Charge -> Kz 84/85, 67
    EU_ERWERB = "eu_erwerb"  # innergemeinschaftlicher Erwerb von Waren -> Kz 89/93, 61
    OHNE_VORSTEUER = "ohne_vorsteuer"  # keine USt auf der Rechnung, nichts für die UStVA
    # beide Richtungen
    IGNORIEREN = "ignorieren"  # privat, Duplikat, kein Rechnungsbeleg


@dataclass(frozen=True)
class SteuerfallInfo:
    art: Art | None
    titel: str
    kennzahlen: str
    sonderfall: bool  # Sonderfall -> Hinweis "im Zweifel Steuerberater fragen"


STEUERFAELLE: dict[Steuerfall, SteuerfallInfo] = {
    Steuerfall.INLAND: SteuerfallInfo(Art.AUSGANG, "Inland, steuerpflichtig", "81 / 86 / 35+36", False),
    Steuerfall.EU_LEISTUNG: SteuerfallInfo(
        Art.AUSGANG, "Sonstige Leistung an EU-Unternehmer (Reverse Charge beim Kunden)", "21", True
    ),
    Steuerfall.EU_LIEFERUNG: SteuerfallInfo(Art.AUSGANG, "Innergemeinschaftliche Lieferung (Waren)", "41", True),
    Steuerfall.DRITTLAND_LEISTUNG: SteuerfallInfo(
        Art.AUSGANG, "Leistung an Kunden im Drittland (nicht steuerbar)", "45", True
    ),
    Steuerfall.AUSFUHR: SteuerfallInfo(Art.AUSGANG, "Ausfuhrlieferung (steuerfrei)", "43", True),
    Steuerfall.VORSTEUER_INLAND: SteuerfallInfo(Art.EINGANG, "Rechnung mit deutscher Umsatzsteuer", "66", False),
    Steuerfall.EU_LEISTUNG_13B: SteuerfallInfo(
        Art.EINGANG, "Leistung eines EU-Unternehmers (Reverse Charge)", "46 / 47 / 67", True
    ),
    Steuerfall.AUSLAND_13B: SteuerfallInfo(
        Art.EINGANG, "Leistung eines Drittland-Unternehmers (Reverse Charge)", "84 / 85 / 67", True
    ),
    Steuerfall.EU_ERWERB: SteuerfallInfo(
        Art.EINGANG, "Innergemeinschaftlicher Erwerb (Waren aus der EU)", "89 / 93 / 61", True
    ),
    Steuerfall.OHNE_VORSTEUER: SteuerfallInfo(Art.EINGANG, "Ohne Umsatzsteuer (z. B. Kleinunternehmer)", "–", False),
    Steuerfall.IGNORIEREN: SteuerfallInfo(None, "Nicht berücksichtigen", "–", False),
}


@dataclass(frozen=True)
class Kennzahl:
    nummer: str
    titel: str
    betrag_art: str  # "bemessungsgrundlage" (volle Euro) oder "steuer" (Euro und Cent)
    von_elster_berechnet: bool = False


KENNZAHLEN: dict[str, Kennzahl] = {
    k.nummer: k
    for k in [
        Kennzahl("81", "Steuerpflichtige Umsätze zu 19 %", "bemessungsgrundlage"),
        Kennzahl("81-steuer", "Steuer auf Kz 81", "steuer", von_elster_berechnet=True),
        Kennzahl("86", "Steuerpflichtige Umsätze zu 7 %", "bemessungsgrundlage"),
        Kennzahl("86-steuer", "Steuer auf Kz 86", "steuer", von_elster_berechnet=True),
        Kennzahl("35", "Umsätze zu anderen Steuersätzen", "bemessungsgrundlage"),
        Kennzahl("36", "Steuer auf Kz 35", "steuer"),
        Kennzahl("41", "Innergemeinschaftliche Lieferungen an Abnehmer mit USt-IdNr.", "bemessungsgrundlage"),
        Kennzahl("43", "Weitere steuerfreie Umsätze mit Vorsteuerabzug (z. B. Ausfuhr)", "bemessungsgrundlage"),
        Kennzahl("89", "Innergemeinschaftliche Erwerbe zu 19 %", "bemessungsgrundlage"),
        Kennzahl("89-steuer", "Steuer auf Kz 89", "steuer", von_elster_berechnet=True),
        Kennzahl("93", "Innergemeinschaftliche Erwerbe zu 7 %", "bemessungsgrundlage"),
        Kennzahl("93-steuer", "Steuer auf Kz 93", "steuer", von_elster_berechnet=True),
        Kennzahl("46", "Sonstige Leistungen eines im EU-Ausland ansässigen Unternehmers (§ 13b Abs. 1 UStG)",
                 "bemessungsgrundlage"),
        Kennzahl("47", "Steuer auf Kz 46", "steuer"),
        Kennzahl("84", "Andere Leistungen nach § 13b UStG (z. B. Drittland-Anbieter)", "bemessungsgrundlage"),
        Kennzahl("85", "Steuer auf Kz 84", "steuer"),
        Kennzahl("21", "Nicht steuerbare sonstige Leistungen im EU-Ausland (§ 18b Satz 1 Nr. 2 UStG)",
                 "bemessungsgrundlage"),
        Kennzahl("45", "Übrige nicht steuerbare Umsätze (Leistungsort nicht im Inland)", "bemessungsgrundlage"),
        Kennzahl("66", "Vorsteuer aus Rechnungen anderer Unternehmer", "steuer"),
        Kennzahl("61", "Vorsteuer aus innergemeinschaftlichem Erwerb", "steuer"),
        Kennzahl("67", "Vorsteuer aus Leistungen nach § 13b UStG", "steuer"),
        Kennzahl("83", "Verbleibende Umsatzsteuer-Vorauszahlung (Zahllast) bzw. Überschuss (−)", "steuer",
                 von_elster_berechnet=True),
    ]
}
