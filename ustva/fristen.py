"""Abgabefristen der monatlichen UStVA.

Abgabe und Zahlung bis zum 10. des Folgemonats (§ 18 Abs. 1 UStG). Fällt der
Tag auf Samstag, Sonntag oder einen gesetzlichen Feiertag, verschiebt sich die
Frist auf den nächsten Werktag (§ 108 Abs. 3 AO). Mit Dauerfristverlängerung
verlängert sich die Frist um einen Monat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

MONATSNAMEN = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def ostersonntag(jahr: int) -> date:
    """Gaußsche Osterformel (anonymer gregorianischer Algorithmus)."""
    a = jahr % 19
    b, c = divmod(jahr, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    monat, tag = divmod(h + l_ - 7 * m + 114, 31)
    return date(jahr, monat, tag + 1)


def feiertage(jahr: int, bundesland: str = "") -> dict[date, str]:
    """Gesetzliche Feiertage: bundesweit und, falls angegeben, im Bundesland."""
    ostern = ostersonntag(jahr)
    tage = {
        date(jahr, 1, 1): "Neujahr",
        ostern - timedelta(days=2): "Karfreitag",
        ostern + timedelta(days=1): "Ostermontag",
        date(jahr, 5, 1): "Tag der Arbeit",
        ostern + timedelta(days=39): "Christi Himmelfahrt",
        ostern + timedelta(days=50): "Pfingstmontag",
        date(jahr, 10, 3): "Tag der Deutschen Einheit",
        date(jahr, 12, 25): "1. Weihnachtsfeiertag",
        date(jahr, 12, 26): "2. Weihnachtsfeiertag",
    }
    bl = bundesland.upper()
    if bl in {"BW", "BY", "ST"}:
        tage[date(jahr, 1, 6)] = "Heilige Drei Könige"
    if bl == "BE" or (bl == "MV" and jahr >= 2023):
        tage[date(jahr, 3, 8)] = "Internationaler Frauentag"
    if bl in {"BW", "BY", "HE", "NW", "RP", "SL"}:
        tage[ostern + timedelta(days=60)] = "Fronleichnam"
    if bl == "SL":
        tage[date(jahr, 8, 15)] = "Mariä Himmelfahrt"
    if bl == "TH":
        tage[date(jahr, 9, 20)] = "Weltkindertag"
    if bl in {"BB", "MV", "SN", "ST", "TH", "HB", "HH", "NI", "SH"}:
        tage[date(jahr, 10, 31)] = "Reformationstag"
    if bl in {"BW", "BY", "NW", "RP", "SL"}:
        tage[date(jahr, 11, 1)] = "Allerheiligen"
    if bl == "SN":
        # Mittwoch vor dem 23. November
        d = date(jahr, 11, 22)
        while d.weekday() != 2:
            d -= timedelta(days=1)
        tage[d] = "Buß- und Bettag"
    return tage


def naechster_werktag(tag: date, bundesland: str = "") -> date:
    while tag.weekday() >= 5 or tag in feiertage(tag.year, bundesland):
        tag += timedelta(days=1)
    return tag


def monat_plus(jahr: int, monat: int, n: int) -> tuple[int, int]:
    idx = jahr * 12 + (monat - 1) + n
    return idx // 12, idx % 12 + 1


def abgabefrist(jahr: int, monat: int, bundesland: str = "", dauerfrist: bool = False) -> date:
    fj, fm = monat_plus(jahr, monat, 2 if dauerfrist else 1)
    return naechster_werktag(date(fj, fm, 10), bundesland)


@dataclass
class Frist:
    jahr: int
    monat: int
    faellig: date
    heute: date

    @property
    def zeitraum(self) -> str:
        return f"{self.jahr:04d}-{self.monat:02d}"

    @property
    def tage_bis(self) -> int:
        return (self.faellig - self.heute).days

    def to_dict(self) -> dict:
        return {
            "zeitraum": self.zeitraum,
            "zeitraum_text": f"{MONATSNAMEN[self.monat - 1]} {self.jahr}",
            "faellig": self.faellig.isoformat(),
            "faellig_text": f"{WOCHENTAGE[self.faellig.weekday()]}, {self.faellig:%d.%m.%Y}",
            "tage_bis": self.tage_bis,
        }


def naechste_frist(heute: date, bundesland: str = "", dauerfrist: bool = False) -> Frist:
    """Die nächste noch offene Frist: der älteste Zeitraum, dessen Frist heute oder später endet."""
    start = 2 if dauerfrist else 1
    for zurueck in range(start, -1, -1):
        j, m = monat_plus(heute.year, heute.month, -zurueck)
        faellig = abgabefrist(j, m, bundesland, dauerfrist)
        if faellig >= heute:
            return Frist(j, m, faellig, heute)
    raise AssertionError("Die Frist des laufenden Monats liegt immer in der Zukunft")
