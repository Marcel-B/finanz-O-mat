"""Umzug aus den Export-Dateien der sevDesk-Oberfläche (für Tarife ohne API-Zugang).

sevDesk exportiert unter „Belege“ und „Rechnungen“ jeweils eine CSV und ein ZIP mit den Dateien.
Entpackt in einen Ordner sieht das so aus::

    voucher.csv      Belege: Kopfzeile, darunter die Positionen (Kategorie, Steuersatz), Beträge brutto
    invoices.csv     Ausgangsrechnungen (Netto, Brutto, Zahlungsdatum)
    contacts.csv     Kunden und Lieferanten (optional)
    Belege_*/        2024-03-15_Ausgabebeleg_<Belegnummer>_<md5>.pdf
    Rechnungen_*/    2024-03-15_Rechnung_<Rechnungsnummer>_<md5>.pdf

Die CSV enthält keine Steuerregel. Der Steuerfall ergibt sich deshalb aus Kategorie, Steuersatz und
Anbieter; unsichere Fälle bekommen einen Hinweis. Die Zahlungen ans Finanzamt (Kategorie
„Umsatzsteuer-Vorauszahlungen“) werden nicht als Beleg übernommen, sondern dienen als abgegebene
Zahllast für den Abgleich.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .extraktion import betrag_parsen
from .sevdesk import EU_LAENDER, STEUERREGELN, Kandidat, Regel, SevdeskFehler, _belege, _datum
from .steuerfaelle import Art, Steuerfall

RE_BELEG_DATEI = re.compile(r"^(\d{4}-\d{2}-\d{2})_(Ausgabebeleg|Einnahmebeleg)_(.*)_[0-9a-f]{32}\.\w+$")
RE_RECHNUNG_DATEI = re.compile(r"^(\d{4}-\d{2}-\d{2})_Rechnung_(.*)_[0-9a-f]{32}\.\w+$")

# Kategorien, die nicht in die UStVA gehören und nicht übernommen werden
RE_PRIVAT = re.compile(r"privat|einkommensteuer", re.I)
RE_UST_ZAHLUNG = re.compile(r"umsatzsteuer-(voraus|nach|erstatt)", re.I)
# Ausgaben ohne Umsatzsteuer, bei denen auch ein ausländischer Anbieter kein Reverse Charge bedeutet
RE_OHNE_UST = re.compile(r"lohn|löhne|gehalt|gehälter|versicherung|kontoführung|kartengebühr|abgaben|beitr", re.I)

EU_NAMEN = {
    "belgien", "bulgarien", "dänemark", "estland", "finnland", "frankreich", "griechenland", "irland", "italien",
    "kroatien", "lettland", "litauen", "luxemburg", "malta", "niederlande", "österreich", "polen", "portugal",
    "rumänien", "schweden", "slowakei", "slowenien", "spanien", "tschechien", "tschechische republik", "ungarn",
    "zypern",
}
RE_EU_NAME = re.compile(r"ireland|irland|luxemb|s\.?\s?à\.?\s?r\.?\s?l|s\.?a\.?r\.?l|s\.r\.o\.|\bb\.v\.|\bn\.v\.", re.I)
RE_AUSLAND_NAME = re.compile(r"\b(inc|llc|ltd|limited|corp|pte|co\.,? ltd)\b\.?", re.I)

MONATE = {
    "jan": 1, "feb": 2, "mrz": 3, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "okt": 10, "nov": 11, "dez": 12,
}
RE_UST_MONAT = re.compile(r"umsatzsteuer\s+(jan|feb|mrz|mär|mar|apr|mai|jun|jul|aug|sep|okt|nov|dez)\w*\.?\s*(\d{2,4})",
                          re.I)
RE_UST_QUARTAL = re.compile(r"([1-4])\.\s*vj\.?\s*(\d{2,4})", re.I)
RE_NR_MONAT = re.compile(r"(\d{1,2})[./](\d{4})")
RE_NR_QUARTAL = re.compile(r"\bQ([1-4])\b", re.I)

UNPLAUSIBEL = timedelta(days=365)


def _norm(nummer: str) -> str:
    return re.sub(r"[^0-9a-z]", "", nummer.lower())


def _zahl(text: str) -> Decimal:
    wert = betrag_parsen((text or "").replace("€", ""))
    return wert if wert is not None else Decimal(0)


def _satz(text: str) -> Decimal:
    satz = _zahl(text)
    return satz.quantize(Decimal(1)) if satz == satz.to_integral_value() else satz.normalize()


def _csv(pfad: Path) -> list[dict[str, str]]:
    """Liest eine sevDesk-CSV (Semikolon, UTF-8, teils mit doppeltem BOM) mit bereinigten Spaltennamen."""
    text = pfad.read_text(encoding="utf-8-sig").lstrip("﻿")
    zeilen = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not zeilen:
        return []
    kopf = [k.strip() for k in zeilen[0]]
    return [{k: (z[i].strip() if i < len(z) else "") for i, k in enumerate(kopf) if k} for z in zeilen[1:] if any(z)]


def _monat_vorher(tag: date) -> str:
    erster = tag.replace(day=1) - timedelta(days=1)
    return f"{erster:%Y-%m}"


def _jahr(text: str) -> int:
    jahr = int(text)
    return jahr + 2000 if jahr < 100 else jahr


@dataclass
class ExportBeleg:
    """Ein Beleg aus voucher.csv: die Kopfzeile und ihre Positionen."""

    kopf: dict[str, str]
    positionen: list[dict[str, str]] = field(default_factory=list)

    @property
    def nummer(self) -> str:
        return self.kopf.get("Belegnummer", "")

    @property
    def datum(self) -> date | None:
        return _datum(self.kopf.get("Datum"))

    @property
    def kategorien(self) -> str:
        return ", ".join(dict.fromkeys(p.get("Kategorie", "") for p in self.positionen if p.get("Kategorie")))

    @property
    def beschreibung(self) -> str:
        texte = [self.kopf.get("Beschreibung", "")] + [p.get("Beschreibung", "") for p in self.positionen]
        return " ".join(dict.fromkeys(t for t in texte if t))


def belege_lesen(pfad: Path) -> list[ExportBeleg]:
    """voucher.csv: eine Zeile ohne Positionsnummer beginnt einen Beleg, die folgenden sind seine Positionen.

    Belegnummern sind nicht eindeutig („Privat“, „Lohn“), deshalb zählt die Reihenfolge.
    """
    belege: list[ExportBeleg] = []
    for zeile in _csv(pfad):
        if not zeile.get("Position"):
            belege.append(ExportBeleg(zeile))
        elif belege:
            belege[-1].positionen.append(zeile)
    return belege


@dataclass
class Export:
    ordner: Path
    belege: list[ExportBeleg]
    rechnungen: list[dict[str, str]]
    kontakte: dict[str, dict[str, str]]  # Name (klein) und Kundennummer -> Zeile
    beleg_dateien: dict[tuple[str, str], list[tuple[Path, str]]]  # (Datum, Nummer) -> [(Datei, Richtung)]
    rechnung_dateien: dict[str, list[Path]]
    erstellt: str = ""

    @classmethod
    def ist_export(cls, ordner: Path) -> bool:
        return (ordner / "voucher.csv").exists() or (ordner / "invoices.csv").exists()

    @classmethod
    def laden(cls, ordner: Path) -> Export:
        if not cls.ist_export(ordner):
            raise SevdeskFehler(f"In {ordner} liegt kein sevDesk-Export (voucher.csv bzw. invoices.csv fehlt)")
        belege = belege_lesen(ordner / "voucher.csv") if (ordner / "voucher.csv").exists() else []
        rechnungen = _csv(ordner / "invoices.csv") if (ordner / "invoices.csv").exists() else []
        kontakte: dict[str, dict[str, str]] = {}
        if (ordner / "contacts.csv").exists():
            for k in _csv(ordner / "contacts.csv"):
                name = k.get("Organisation") or " ".join(t for t in (k.get("Vorname"), k.get("Nachname")) if t)
                if name:
                    kontakte.setdefault(name.lower(), k)
                if k.get("Kunden-Nr.") and k.get("Organisation"):
                    kontakte.setdefault(f"#{k['Kunden-Nr.']}", k)
        beleg_dateien: dict[tuple[str, str], list[tuple[Path, str]]] = {}
        rechnung_dateien: dict[str, list[Path]] = {}
        for pfad in sorted(ordner.rglob("*")):
            if not pfad.is_file():
                continue
            if m := RE_BELEG_DATEI.match(pfad.name):
                beleg_dateien.setdefault((m[1], _norm(m[3])), []).append((pfad, m[2]))
            elif m := RE_RECHNUNG_DATEI.match(pfad.name):
                rechnung_dateien.setdefault(_norm(m[2]), []).append(pfad)
        quellen = [p for p in (ordner / "voucher.csv", ordner / "invoices.csv") if p.exists()]
        erstellt = max(datetime.fromtimestamp(p.stat().st_mtime) for p in quellen).strftime("%d.%m.%Y")
        return cls(ordner, belege, rechnungen, kontakte, beleg_dateien, rechnung_dateien, erstellt)

    # --- Belege ---------------------------------------------------------------------------------

    def _kontakt(self, name: str) -> dict[str, str]:
        return self.kontakte.get(name.lower(), {})

    def _ausland(self, name: str) -> str:
        """"EU", "Drittland" oder "" (inländisch bzw. nicht erkennbar)."""
        kontakt = self._kontakt(name)
        ustid = kontakt.get("UmSt.-ID", "").replace(" ", "").upper()
        land = kontakt.get("Land", "").strip().lower()
        if ustid[:2] in EU_LAENDER or land in EU_NAMEN or RE_EU_NAME.search(name):
            return "EU"
        if (land and land != "deutschland") or RE_AUSLAND_NAME.search(name):
            return "Drittland"
        return ""

    def _regel(self, kategorie: str, satz: Decimal, art: Art, partner: str) -> Regel:
        if art == Art.AUSGANG:
            if satz:
                return STEUERREGELN["1"]
            return Regel("Einnahme ohne Umsatzsteuer", art, Steuerfall.IGNORIEREN,
                         "sevDesk-Export: Einnahme mit 0 % ohne Steuerregel; bitte Steuerfall prüfen.")
        if satz:
            return STEUERREGELN["9"]
        if RE_OHNE_UST.search(kategorie):
            return STEUERREGELN["10"]
        ausland = self._ausland(partner)
        if ausland:
            kz = "46/47" if ausland == "EU" else "84/85"
            return Regel(f"Reverse Charge ({ausland})", art,
                         Steuerfall.EU_LEISTUNG_13B if ausland == "EU" else Steuerfall.AUSLAND_13B,
                         f"sevDesk-Export: 0 % bei Anbieter im Ausland ({ausland}), als Reverse Charge übernommen "
                         f"(Kz {kz} und 67, Zahllast unverändert); bitte prüfen.")
        if re.search(r"lizenz|software|dienstleist|fremdleist", kategorie, re.I):
            return Regel("Ohne Vorsteuer", art, Steuerfall.OHNE_VORSTEUER,
                         "sevDesk-Export: 0 % ohne erkennbaren Auslandsbezug; falls der Anbieter im Ausland sitzt, "
                         "auf Reverse Charge ändern.")
        return STEUERREGELN["10"]

    def _kandidat_beleg(self, b: ExportBeleg, sevdesk_id: str, dateien: list[tuple[Path, str]]) -> Kandidat:
        partner = b.kopf.get("Lieferant / Kunde", "")
        datei, richtung = dateien.pop(0) if dateien else (None, "")
        k = Kandidat("Voucher", sevdesk_id, " ".join(t for t in (f"Beleg {b.nummer or sevdesk_id}", partner) if t),
                     datei=datei)
        kategorien = b.kategorien
        if RE_PRIVAT.search(kategorien):
            k.grund = "Privat bzw. Einkommensteuer (gehört nicht in die UStVA)"
            return k
        if RE_UST_ZAHLUNG.search(kategorien):
            k.grund = "Umsatzsteuer-Zahlung ans Finanzamt (dient als abgegebene Zahllast für den Abgleich)"
            return k
        if not b.positionen:
            k.grund = "Ohne Positionen"
            return k
        einnahme = richtung == "Einnahmebeleg" or bool(re.search(r"erlös|einnahme", kategorien, re.I))
        art = Art.AUSGANG if einnahme else Art.EINGANG
        rechnungsdatum, zahlungsdatum = b.datum, _datum(b.kopf.get("Bezahlt am"))
        hinweise = []
        if rechnungsdatum and zahlungsdatum and zahlungsdatum - rechnungsdatum > UNPLAUSIBEL:
            # Datum wie in sevDesk lassen: so hat sevDesk die Vorsteuer auch angemeldet
            hinweise.append(f"sevDesk-Belegdatum {rechnungsdatum:%d.%m.%Y} liegt über ein Jahr vor der Zahlung "
                            f"({zahlungsdatum:%d.%m.%Y}); bitte Rechnungsdatum prüfen.")
        # Positionen nach Steuerfall und Satz zusammenfassen; Beträge sind brutto
        gruppen: dict[Regel, dict[Decimal, tuple[Decimal, Decimal | None]]] = {}
        for p in b.positionen:
            satz = _satz(p.get("Umsatzsteuersatz", ""))
            regel = self._regel(p.get("Kategorie", ""), satz, art, partner)
            brutto = _zahl(p.get("Betrag", ""))
            netto = (brutto * 100 / (100 + satz)).quantize(Decimal("0.01"))
            alt_netto, alt_steuer = gruppen.setdefault(regel, {}).get(satz, (Decimal(0), Decimal(0)))
            gruppen[regel][satz] = (alt_netto + netto, (alt_steuer or Decimal(0)) + brutto - netto)
        kontakt = self._kontakt(partner)
        for regel, summen in gruppen.items():
            k.belege += _belege(
                regel, art, summen, rechnungsdatum=rechnungsdatum, zahlungsdatum=zahlungsdatum, partner=partner,
                partner_ustid=kontakt.get("UmSt.-ID", "").replace(" ", "").upper(), rechnungsnummer=b.nummer,
                beschreibung=b.beschreibung or kategorien or f"sevDesk {b.nummer}",
            )
        for beleg in k.belege:
            beleg.hinweise.extend(hinweise)
        return k

    def _kandidat_rechnung(self, r: dict[str, str], sevdesk_id: str, dateien: list[Path]) -> Kandidat:
        nummer = r.get("Rechnungs-Nr.", "")
        kontakt = self.kontakte.get(f"#{r.get('Empfänger-KdNr', '')}", {})
        adresse = r.get("Empfänger-Adresse", "")
        partner = kontakt.get("Organisation") or (adresse.splitlines()[0].strip() if adresse else "")
        k = Kandidat("Invoice", sevdesk_id, " ".join(t for t in (f"Rechnung {nummer or sevdesk_id}", partner) if t),
                     datei=dateien.pop(0) if dateien else None)
        netto, brutto = _zahl(r.get("Gesamtbetrag-Netto", "")), _zahl(r.get("Gesamtbetrag-Brutto", ""))
        if not netto and not brutto:
            return k  # wird „Ohne Beträge“
        steuer = brutto - netto
        satz = Decimal(0)
        if netto and steuer:
            satz = min((Decimal(19), Decimal(7)), key=lambda s: abs(steuer / netto * 100 - s))
        regel = STEUERREGELN["1"]
        land = r.get("Empfänger-Land", "").strip()
        hinweise = []
        if land and land.lower() != "deutschland":
            hinweise.append(f"Kunde in {land}; Steuerfall bitte prüfen.")
        k.belege = _belege(
            regel, Art.AUSGANG, {satz: (netto, steuer)},
            rechnungsdatum=_datum(r.get("Rechnungs-Datum")), zahlungsdatum=_datum(r.get("Bezahlt am")),
            partner=partner, partner_ustid=kontakt.get("UmSt.-ID", "").replace(" ", "").upper(),
            rechnungsnummer=nummer, beschreibung=r.get("Betreff") or f"sevDesk Rechnung {nummer}",
        )
        if _zahl(r.get("offener Betrag", "")) and _datum(r.get("Bezahlt am")):
            hinweise.append("sevDesk: nur teilweise bezahlt; Zahlungsdatum bitte prüfen.")
        for beleg in k.belege:
            beleg.hinweise.extend(hinweise)
        return k

    def kandidaten(self) -> list[Kandidat]:
        # Gleiche Nummer am gleichen Tag kommt vor („Privat“): Dateien der Reihe nach vergeben, IDs durchzählen
        beleg_dateien = {s: list(v) for s, v in self.beleg_dateien.items()}
        rechnung_dateien = {s: list(v) for s, v in self.rechnung_dateien.items()}
        gezaehlt: dict[str, int] = {}

        def schluessel(art: str, datum: date | None, nummer: str) -> str:
            basis = f"{datum:%Y-%m-%d}_{_norm(nummer) or 'ohne'}" if datum else _norm(nummer) or "ohne"
            gezaehlt[f"{art}/{basis}"] = n = gezaehlt.get(f"{art}/{basis}", 0) + 1
            return basis if n == 1 else f"{basis}-{n}"

        ergebnis = []
        for b in self.belege:
            tag = f"{b.datum:%Y-%m-%d}" if b.datum else ""
            ergebnis.append(self._kandidat_beleg(b, schluessel("Voucher", b.datum, b.nummer),
                                                 beleg_dateien.get((tag, _norm(b.nummer)), [])))
        for r in self.rechnungen:
            nummer = r.get("Rechnungs-Nr.", "")
            ergebnis.append(self._kandidat_rechnung(r, schluessel("Invoice", _datum(r.get("Rechnungs-Datum")), nummer),
                                                    rechnung_dateien.get(_norm(nummer), [])))
        for k in ergebnis:
            if not k.grund and not k.belege:
                k.grund = "Ohne Beträge"
        return ergebnis

    # --- Abgegebene Zahllast --------------------------------------------------------------------

    def abgegeben(self) -> tuple[dict[str, Decimal], list[str]]:
        """Zahllast je Zeitraum aus den Zahlungen ans Finanzamt; zweiter Wert: nicht zuordenbare Zahlungen.

        Zeitraum ist ``JJJJ-MM`` oder bei vierteljährlicher Abgabe ``JJJJ-Qn``. Maßgeblich ist der Text der
        Überweisung („Umsatzsteuer Mai 25“, „4.Vj.23“), sonst die Belegnummer („UStVA-07.2026“), sonst der Monat
        vor der Zahlung.
        """
        werte: dict[str, Decimal] = {}
        offen: list[str] = []
        for b in self.belege:
            if not RE_UST_ZAHLUNG.search(b.kategorien):
                continue
            text, datum = b.beschreibung, b.datum
            betrag = _zahl(b.kopf.get("Betrag", ""))
            datei = self.beleg_dateien.get((f"{datum:%Y-%m-%d}" if datum else "", _norm(b.nummer)))
            if datei and datei[0][1] == "Einnahmebeleg":  # Erstattung vom Finanzamt
                betrag = -betrag
            zeitraum = ""
            if m := RE_UST_MONAT.search(text):
                zeitraum = f"{_jahr(m[2]):04d}-{MONATE[m[1].lower()[:3]]:02d}"
            elif m := RE_UST_QUARTAL.search(text):
                zeitraum = f"{_jahr(m[2]):04d}-Q{m[1]}"
            elif re.search(r"umsatzsteuer\s+\d{2,4}\b|erstattung|jahr", f"{text} {b.nummer}", re.I):
                zeitraum = ""  # Jahreserklärung, gehört nicht zu einer Voranmeldung
            elif m := RE_NR_MONAT.search(b.nummer):
                zeitraum = f"{int(m[2]):04d}-{int(m[1]):02d}"
            elif (m := RE_NR_QUARTAL.search(b.nummer)) and datum:
                vorher = date.fromisoformat(_monat_vorher(datum) + "-01")
                zeitraum = f"{vorher.year:04d}-Q{m[1]}"
            elif datum:
                zeitraum = _monat_vorher(datum)
            if not zeitraum:
                offen.append(f"{b.nummer} vom {datum:%d.%m.%Y}: {betrag} €" if datum else b.nummer)
                continue
            werte[zeitraum] = werte.get(zeitraum, Decimal(0)) + betrag
        return werte, offen
