"""Berechnung der UStVA-Kennzahlen für einen Monat."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .models import Beleg
from .steuerfaelle import KENNZAHLEN, STEUERFAELLE, Art, Steuerfall
from .waehrung import KursFehlt, Umrechner

CENT = Decimal("0.01")
NULL = Decimal(0)

# Reihenfolge wie im Formular
REIHENFOLGE = [
    "81", "81-steuer", "86", "86-steuer", "35", "36", "41", "43", "21", "45",
    "89", "89-steuer", "93", "93-steuer", "46", "47", "84", "85", "66", "61", "67", "83",
]


def cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


def volle_euro(x: Decimal) -> Decimal:
    """Bemessungsgrundlagen werden in ELSTER ohne Cent eingetragen (abgerundet)."""
    return x.quantize(Decimal(1), ROUND_DOWN)


def stichtag(b: Beleg, versteuerung: str) -> date | None:
    """Datum, das über den Voranmeldungszeitraum entscheidet.

    Soll-Versteuerung: Rechnungsdatum. Ist-Versteuerung: bei steuerpflichtigen
    Inlandsumsätzen der Zahlungseingang. Vorsteuer und Reverse Charge richten sich
    immer nach der Rechnung.
    """
    if versteuerung == "ist" and b.art == Art.AUSGANG and b.steuerfall == Steuerfall.INLAND:
        return b.zahlungsdatum
    return b.rechnungsdatum


def zeitraum(b: Beleg, versteuerung: str) -> str | None:
    tag = stichtag(b, versteuerung)
    return f"{tag:%Y-%m}" if tag else None


@dataclass
class Hinweis:
    stufe: str  # "warnung" | "info" | "sonderfall"
    text: str


@dataclass
class BelegZeile:
    beleg: Beleg
    netto_eur: Decimal | None = None
    steuer_eur: Decimal | None = None
    kurs: Decimal | None = None
    kursquelle: str = ""
    fehler: str = ""

    def to_dict(self) -> dict:
        return {
            **self.beleg.to_dict(),
            "netto_eur": _s(self.netto_eur),
            "steuer_eur": _s(self.steuer_eur),
            "kurs_verwendet": _s(self.kurs),
            "kursquelle": self.kursquelle,
            "fehler": self.fehler,
            "steuerfall_titel": STEUERFAELLE[self.beleg.steuerfall].titel,
            "kennzahlen": STEUERFAELLE[self.beleg.steuerfall].kennzahlen,
        }


@dataclass
class Posten:
    kz: str
    betrag: Decimal

    def to_dict(self) -> dict:
        k = KENNZAHLEN[self.kz]
        return {
            "kz": self.kz.split("-")[0],
            "id": self.kz,
            "titel": k.titel,
            "betrag": str(self.betrag),
            "betrag_art": k.betrag_art,
            "von_elster_berechnet": k.von_elster_berechnet,
            "ist_steuerzeile": "-steuer" in self.kz,
        }


@dataclass
class Bericht:
    zeitraum: str
    versteuerung: str
    posten: list[Posten]
    umsatzsteuer: Decimal
    vorsteuer: Decimal
    zahllast: Decimal
    zeilen: list[BelegZeile]
    hinweise: list[Hinweis] = field(default_factory=list)
    zm: list[dict] = field(default_factory=list)

    @property
    def vollstaendig(self) -> bool:
        return not any(z.fehler for z in self.zeilen)

    def wert(self, kz: str) -> Decimal:
        for p in self.posten:
            if p.kz == kz:
                return p.betrag
        return NULL

    def to_dict(self) -> dict:
        return {
            "zeitraum": self.zeitraum,
            "versteuerung": self.versteuerung,
            "kennzahlen": [p.to_dict() for p in self.posten],
            "umsatzsteuer": str(self.umsatzsteuer),
            "vorsteuer": str(self.vorsteuer),
            "zahllast": str(self.zahllast),
            "vollstaendig": self.vollstaendig,
            "hinweise": [{"stufe": h.stufe, "text": h.text} for h in self.hinweise],
            "zm": self.zm,
            "belege": [z.to_dict() for z in self.zeilen],
        }


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def berechnen(
    belege: list[Beleg], jahr: int, monat: int, umrechner: Umrechner, versteuerung: str = "soll"
) -> Bericht:
    ziel = f"{jahr:04d}-{monat:02d}"
    hinweise: list[Hinweis] = []
    zeilen: list[BelegZeile] = []

    bemessung: dict[str, Decimal] = defaultdict(lambda: NULL)  # exakte Summen in EUR
    steuer: dict[str, Decimal] = defaultdict(lambda: NULL)
    zm: dict[tuple[str, str], Decimal] = defaultdict(lambda: NULL)
    sonderfaelle: set[Steuerfall] = set()
    kurse: dict[str, tuple[Decimal, str]] = {}

    for b in belege:
        if zeitraum(b, versteuerung) != ziel:
            continue
        zeile = BelegZeile(b)
        zeilen.append(zeile)
        if b.steuerfall == Steuerfall.IGNORIEREN:
            continue
        try:
            netto_eur, kurs, quelle = umrechner.in_euro(b.netto, b.waehrung, stichtag(b, versteuerung), b.kurs)
        except KursFehlt as e:
            zeile.fehler = str(e)
            continue
        zeile.netto_eur, zeile.kurs, zeile.kursquelle = netto_eur, kurs, quelle
        zeile.steuer_eur = cent(b.steuer_in_belegwaehrung() / kurs)
        if b.waehrung.upper() != "EUR":
            kurse[b.waehrung.upper()] = (kurs, quelle)
        if STEUERFAELLE[b.steuerfall].sonderfall:
            sonderfaelle.add(b.steuerfall)

        satz = b.steuersatz
        fall = b.steuerfall
        if fall == Steuerfall.INLAND:
            if satz == 19:
                bemessung["81"] += netto_eur
            elif satz == 7:
                bemessung["86"] += netto_eur
            elif satz == 0:
                zeile.fehler = "Steuersatz 0 % im Inland wird nicht unterstützt (Kz 87), bitte Steuerfall prüfen"
            else:
                bemessung["35"] += netto_eur
                steuer["36"] += netto_eur * satz / 100
        elif fall == Steuerfall.EU_LEISTUNG:
            bemessung["21"] += netto_eur
            zm[(b.partner_ustid or "", "S")] += netto_eur
        elif fall == Steuerfall.EU_LIEFERUNG:
            bemessung["41"] += netto_eur
            zm[(b.partner_ustid or "", "L")] += netto_eur
        elif fall == Steuerfall.DRITTLAND_LEISTUNG:
            bemessung["45"] += netto_eur
        elif fall == Steuerfall.AUSFUHR:
            bemessung["43"] += netto_eur
        elif fall == Steuerfall.VORSTEUER_INLAND:
            steuer["66"] += zeile.steuer_eur
        elif fall == Steuerfall.EU_LEISTUNG_13B:
            bemessung["46"] += netto_eur
            steuer["47"] += netto_eur * satz / 100
        elif fall == Steuerfall.AUSLAND_13B:
            bemessung["84"] += netto_eur
            steuer["85"] += netto_eur * satz / 100
        elif fall == Steuerfall.EU_ERWERB:
            if satz == 19:
                bemessung["89"] += netto_eur
            elif satz == 7:
                bemessung["93"] += netto_eur
            else:
                zeile.fehler = ("Innergemeinschaftlicher Erwerb zu anderen Steuersätzen (Kz 95/98) "
                                "wird nicht unterstützt")
        if fall in (Steuerfall.EU_LEISTUNG_13B, Steuerfall.AUSLAND_13B):
            zeile.steuer_eur = cent(netto_eur * satz / 100)

    werte: dict[str, Decimal] = {}
    for kz in ("81", "86", "35", "41", "43", "21", "45", "89", "93", "46", "84"):
        if bemessung[kz]:
            werte[kz] = volle_euro(bemessung[kz])
    # ELSTER berechnet die Steuer aus der eingetragenen (abgerundeten) Bemessungsgrundlage.
    for kz, satz in (("81", 19), ("86", 7), ("89", 19), ("93", 7)):
        if kz in werte:
            werte[f"{kz}-steuer"] = cent(werte[kz] * satz / 100)
    for kz in ("36", "47", "85", "66"):
        if steuer[kz]:
            werte[kz] = cent(steuer[kz])
    if "89-steuer" in werte or "93-steuer" in werte:
        werte["61"] = werte.get("89-steuer", NULL) + werte.get("93-steuer", NULL)
    if "47" in werte or "85" in werte:
        werte["67"] = werte.get("47", NULL) + werte.get("85", NULL)

    umsatzsteuer = sum(
        (werte.get(k, NULL) for k in ("81-steuer", "86-steuer", "36", "89-steuer", "93-steuer", "47", "85")), NULL
    )
    vorsteuer = sum((werte.get(k, NULL) for k in ("66", "61", "67")), NULL)
    zahllast = umsatzsteuer - vorsteuer
    werte["83"] = zahllast

    posten = [Posten(kz, werte[kz]) for kz in REIHENFOLGE if kz in werte]

    # Hinweise
    if versteuerung == "ist":
        unbezahlt = [
            b for b in belege
            if b.art == Art.AUSGANG and b.steuerfall == Steuerfall.INLAND and b.zahlungsdatum is None
            and b.rechnungsdatum is not None and f"{b.rechnungsdatum:%Y-%m}" <= ziel
        ]
        if unbezahlt:
            hinweise.append(Hinweis(
                "info",
                f"Ist-Versteuerung: {len(unbezahlt)} Ausgangsrechnung(en) ohne Zahlungsdatum sind nicht enthalten. "
                "Ist eine davon in diesem Monat bezahlt worden, bitte das Zahlungsdatum eintragen.",
            ))
    fehlerhaft = [z for z in zeilen if z.fehler]
    if fehlerhaft:
        hinweise.append(Hinweis(
            "warnung",
            f"{len(fehlerhaft)} Beleg(e) konnten nicht berücksichtigt werden. Die Werte sind unvollständig, "
            "bitte die markierten Belege korrigieren.",
        ))
    zu_pruefen = [z for z in zeilen if z.beleg.pruefen and z.beleg.steuerfall != Steuerfall.IGNORIEREN]
    if zu_pruefen:
        hinweise.append(Hinweis(
            "warnung",
            f"{len(zu_pruefen)} automatisch eingelesene(r) Beleg(e) sind noch nicht geprüft. "
            "Beträge, Datum und Steuerfall bitte mit der Rechnung vergleichen.",
        ))
    if zm:
        hinweise.append(Hinweis(
            "sonderfall",
            "Leistungen an Unternehmer im EU-Ausland (Kz 21/41) müssen zusätzlich in der Zusammenfassenden "
            "Meldung (ZM) an das Bundeszentralamt für Steuern gemeldet werden, bis zum 25. Tag nach Ablauf des "
            "Meldezeitraums. Die ZM ist nicht Teil der UStVA.",
        ))
        if any(not ustid for ustid, _ in zm):
            hinweise.append(Hinweis(
                "warnung", "Für mindestens einen EU-Kunden fehlt die USt-IdNr. Ohne sie ist die ZM nicht möglich."
            ))
    if "46" in werte or "84" in werte:
        hinweise.append(Hinweis(
            "sonderfall",
            "Reverse Charge: Die Steuer in Kz 47/85 schuldest du selbst. Bei vollem Vorsteuerabzug steht derselbe "
            "Betrag als Vorsteuer in Kz 67, die Zahllast ändert sich dadurch nicht. In ELSTER musst du Kz 47/85 "
            "und Kz 67 selbst eintragen.",
        ))
    if "45" in werte:
        hinweise.append(Hinweis(
            "sonderfall",
            "Kz 45 setzt voraus, dass der Leistungsort im Ausland liegt (z. B. Leistung an ein Unternehmen im "
            "Drittland). Bei Privatkunden im Ausland kann deutsche Umsatzsteuer anfallen.",
        ))
    if "35" in werte:
        hinweise.append(Hinweis("sonderfall", "Umsätze zu anderen Steuersätzen (Kz 35/36) sind ungewöhnlich."))
    for waehrung, (kurs, quelle) in sorted(kurse.items()):
        text = f"Umrechnung {waehrung}: 1 EUR = {kurs} {waehrung} ({quelle})."
        if quelle == "EZB-Monatsmittel":
            text += (" Maßgeblich ist der vom BMF veröffentlichte Monatskurs (§ 16 Abs. 6 UStG); "
                     "weicht er ab, bitte unter Kurse eintragen.")
        hinweise.append(Hinweis("info", text))
    if zahllast < 0:
        hinweise.append(Hinweis("info", "Negativer Wert in Kz 83: Das Finanzamt erstattet den Überschuss."))
    if sonderfaelle:
        titel = ", ".join(sorted(STEUERFAELLE[f].titel for f in sonderfaelle))
        hinweise.append(Hinweis(
            "sonderfall",
            f"Sonderfälle in diesem Monat: {titel}. Die App ersetzt keine Steuerberatung; im Zweifel beim "
            "Finanzamt oder Steuerberater nachfragen.",
        ))

    zm_liste = [
        {"ustid": ustid, "art": "Sonstige Leistung" if art == "S" else "Lieferung", "betrag": str(volle_euro(summe))}
        for (ustid, art), summe in sorted(zm.items())
    ]
    return Bericht(ziel, versteuerung, posten, umsatzsteuer, vorsteuer, zahllast, zeilen, hinweise, zm_liste)
