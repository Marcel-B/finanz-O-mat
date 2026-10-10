"""Umrechnung von Fremdwährungen in Euro.

Nach § 16 Abs. 6 UStG werden Beträge in fremder Währung mit dem vom
Bundesfinanzministerium monatlich bekanntgegebenen Durchschnittskurs des Monats
umgerechnet, in dem die Leistung ausgeführt wurde. Der BMF-Kurs lässt sich in der
Kurstabelle hinterlegen und hat Vorrang. Ist keiner hinterlegt, berechnet die App
als Näherung das Monatsmittel der EZB-Referenzkurse.

Alle Kurse sind in Einheiten der Fremdwährung je 1 EUR angegeben (z. B. 1,17 USD).
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx

from .fristen import monat_plus

EZB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{waehrung}.EUR.SP00.A"
CENT = Decimal("0.01")


class KursSpeicher(Protocol):
    def kurs_lesen(self, waehrung: str, zeitraum: str) -> tuple[Decimal, str] | None: ...

    def kurs_speichern(self, waehrung: str, zeitraum: str, kurs: Decimal, quelle: str) -> None: ...


class KursFehlt(Exception):
    pass


def ezb_monatsmittel(waehrung: str, jahr: int, monat: int, client: httpx.Client | None = None) -> Decimal:
    """Durchschnitt der täglichen EZB-Referenzkurse eines Monats."""
    nj, nm = monat_plus(jahr, monat, 1)
    params = {
        "startPeriod": f"{jahr:04d}-{monat:02d}-01",
        "endPeriod": (date(nj, nm, 1) - timedelta(days=1)).isoformat(),
        "format": "csvdata",
    }
    eigener_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        antwort = client.get(EZB_URL.format(waehrung=waehrung.upper()), params=params)
        antwort.raise_for_status()
    except httpx.HTTPError as e:
        raise KursFehlt(f"EZB-Kurs {waehrung} {jahr}-{monat:02d} nicht abrufbar: {e}") from e
    finally:
        if eigener_client:
            client.close()
    werte = [
        Decimal(zeile["OBS_VALUE"])
        for zeile in csv.DictReader(io.StringIO(antwort.text))
        if zeile.get("OBS_VALUE")
    ]
    if not werte:
        raise KursFehlt(f"Keine EZB-Kurse für {waehrung} im {monat:02d}/{jahr}")
    return (sum(werte) / len(werte)).quantize(Decimal("0.0001"), ROUND_HALF_UP)


class Umrechner:
    def __init__(
        self,
        speicher: KursSpeicher,
        ezb_abrufen: bool = True,
        client: httpx.Client | None = None,
        heute: date | None = None,
    ):
        self.speicher = speicher
        self.ezb_abrufen = ezb_abrufen
        self.client = client
        self._heute = heute

    @property
    def heute(self) -> date:
        return self._heute or date.today()

    def monatskurs(self, waehrung: str, jahr: int, monat: int) -> tuple[Decimal, str]:
        waehrung = waehrung.upper()
        if waehrung == "EUR":
            return Decimal(1), "EUR"
        zeitraum = f"{jahr:04d}-{monat:02d}"
        gespeichert = self.speicher.kurs_lesen(waehrung, zeitraum)
        if gespeichert:
            return gespeichert
        if not self.ezb_abrufen:
            raise KursFehlt(f"Kein Kurs für {waehrung} im {zeitraum} hinterlegt")
        # Der Monat muss abgeschlossen sein, sonst wäre das Mittel unvollständig.
        if (jahr, monat) >= (self.heute.year, self.heute.month):
            raise KursFehlt(f"Monat {zeitraum} ist noch nicht abgeschlossen, Kurs {waehrung} fehlt")
        kurs = ezb_monatsmittel(waehrung, jahr, monat, self.client)
        self.speicher.kurs_speichern(waehrung, zeitraum, kurs, "EZB-Monatsmittel")
        return kurs, "EZB-Monatsmittel"

    def in_euro(self, betrag: Decimal, waehrung: str, stichtag: date, kurs: Decimal | None = None) -> tuple[
        Decimal, Decimal, str
    ]:
        """Gibt (Euro-Betrag, verwendeter Kurs, Kursquelle) zurück."""
        if waehrung.upper() == "EUR":
            return betrag, Decimal(1), "EUR"
        if kurs:
            quelle = "Kurs am Beleg"
        else:
            kurs, quelle = self.monatskurs(waehrung, stichtag.year, stichtag.month)
        return (betrag / kurs).quantize(CENT, ROUND_HALF_UP), kurs, quelle
