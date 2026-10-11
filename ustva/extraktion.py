"""Heuristisches Auslesen von Rechnungstext (PDF-Text oder Paperless-OCR).

Das Ergebnis ist ein Vorschlag. Automatisch erkannte Belege werden immer als
"prüfen" markiert und müssen in der Oberfläche bestätigt werden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from .steuerfaelle import Art, Steuerfall

EU_LAENDER = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU", "IE", "IT",
    "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI",
}

MONATE = {
    "januar": 1, "jan": 1, "january": 1, "februar": 2, "feb": 2, "february": 2, "märz": 3, "maerz": 3,
    "mär": 3, "mar": 3, "march": 3, "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "jun": 6, "june": 6,
    "juli": 7, "jul": 7, "july": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10, "oct": 10, "october": 10, "november": 11, "nov": 11, "dezember": 12,
    "dez": 12, "dec": 12, "december": 12,
}

WAEHRUNGEN = [
    (re.compile(r"US\$|\bUSD\b|\$"), "USD"),
    (re.compile(r"£|\bGBP\b"), "GBP"),
    (re.compile(r"\bCHF\b"), "CHF"),
    (re.compile(r"€|\bEUR\b"), "EUR"),
]

BETRAG = r"[-−]?\s?(?:\d{1,3}(?:[.,']\d{3})+|\d+)(?:[.,]\d{1,2})?"
RE_BETRAG = re.compile(rf"(?<![\w.,])({BETRAG})(?![\d])")
RE_SATZ = re.compile(r"(?<![\d,.])(19|16|7|5|0)(?:[.,]0+)?\s?%")
# Formate der USt-IdNr. je Mitgliedstaat (vereinfacht)
USTID_FORMATE = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}",
    "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}", "FR": r"[A-Z0-9]{2}\d{9}", "HR": r"\d{11}", "HU": r"\d{8}", "IE": r"\d[A-Z0-9+*]\d{5}[A-Z]{1,2}",
    "IT": r"\d{11}", "LT": r"\d{9}(?:\d{3})?", "LU": r"\d{8}", "LV": r"\d{11}", "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"\d{2,10}", "SE": r"\d{12}",
    "SI": r"\d{8}", "SK": r"\d{10}", "XI": r"\d{9}(?:\d{3})?",
}
RE_USTID = re.compile(
    r"(?<![A-Z0-9])(?:" + "|".join(f"{land} ?{fmt}" for land, fmt in USTID_FORMATE.items()) + r")(?![A-Z0-9])"
)
RE_OSS = re.compile(r"\bEU\s?\d{9}\b")
RE_DATUM_DE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})\b")
RE_DATUM_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RE_DATUM_TEXT_DE = re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zäÄ]{3,9})\.?\s+(\d{4})\b")
RE_DATUM_TEXT_EN = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
RE_RECHNUNGSNR = re.compile(
    r"(?:rechnungs?-?\s?(?:nr|nummer)|invoice\s?(?:number|no|#)|beleg-?nr)\.?\s*[:#]?\s*"
    # in Gruppen gesetzte Nummern wie bei der Telekom ("37 1793 3100 0411", "772 634 2750") ganz nehmen
    r"(\d{2,4}(?:[ \u00a0]\d{3,4})+(?![\d.,])|[A-Z0-9][A-Z0-9\-/_.]{2,})",
    re.IGNORECASE,
)

RE_REVERSE_CHARGE = re.compile(
    r"reverse[\s-]?charge|steuerschuldnerschaft des leistungsempf|steuerschuld(?:ner)? (?:geht|ist) .*empf"
    r"|article\s?196|art\.?\s?196|autoliquidation|inversione contabile|inversión del sujeto|verlegd"
    r"|§\s?13b|vat to be accounted for by the recipient",
    re.IGNORECASE,
)
RE_DRITTLAND = re.compile(
    r"\b(united states|usa|u\.s\.a\.|san francisco|delaware|california|new york|washington|seattle"
    r"|switzerland|schweiz|united kingdom|england|london|singapore|canada|australia|israel|japan|norway"
    r"|norwegen|vereinigte staaten)\b",
    re.IGNORECASE,
)

SCHLUESSEL_NETTO = re.compile(
    r"netto|zwischensumme|nettobetrag|summe netto|subtotal|sub-total|net amount|total excl|amount excl"
    r"|total before tax",
    re.IGNORECASE,
)
SCHLUESSEL_STEUER = re.compile(
    r"\b(mwst|mw\.-?st|ust|umsatzsteuer|mehrwertsteuer|vat|tax|steuer)\b", re.IGNORECASE
)
SCHLUESSEL_KEINE_STEUER = re.compile(
    r"ust-?id|steuer-?nr|steuernummer|tax id|vat id|vat no|vat number|vat reg|tax number|ust\.?-?idnr|identifikation",
    re.IGNORECASE,
)
# "Gesamtbetrag inkl. MwSt." ist brutto, obwohl "MwSt" vorkommt -> vor der Steuer prüfen
SCHLUESSEL_BRUTTO = re.compile(
    r"brutto|gesamtbetrag|rechnungsbetrag|endbetrag|zu zahlen|zahlbetrag|amount due|amount paid|balance due"
    r"|inkl|incl|including",
    re.IGNORECASE,
)
SCHLUESSEL_SUMME = re.compile(r"total|gesamt|summe", re.IGNORECASE)
SCHLUESSEL_DATUM = re.compile(
    r"rechnungsdatum|invoice date|date of issue|datum|date issued|ausgestellt|date paid|leistungsdatum",
    re.IGNORECASE,
)


def betrag_parsen(text: str) -> Decimal | None:
    """Liest deutsche (1.234,56) und englische (1,234.56) Schreibweise."""
    t = text.strip().replace("−", "-").replace(" ", "").replace("'", "").replace(" ", "")
    if not t:
        return None
    negativ = t.startswith("-")
    t = t.lstrip("-")
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        teile = t.split(",")
        if len(teile) == 2 and len(teile[1]) in (1, 2):
            t = t.replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "." in t:
        teile = t.split(".")
        if not (len(teile) == 2 and len(teile[1]) in (1, 2)):
            t = t.replace(".", "")
    try:
        wert = Decimal(t)
    except InvalidOperation:
        return None
    return -wert if negativ else wert


def _datum(j: int, m: int, t: int) -> date | None:
    if j < 100:
        j += 2000
    try:
        return date(j, m, t)
    except ValueError:
        return None


def daten_finden(zeile: str) -> list[date]:
    gefunden: list[tuple[int, date]] = []
    for m in RE_DATUM_DE.finditer(zeile):
        if d := _datum(int(m.group(3)), int(m.group(2)), int(m.group(1))):
            gefunden.append((m.start(), d))
    for m in RE_DATUM_ISO.finditer(zeile):
        if d := _datum(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            gefunden.append((m.start(), d))
    for m in RE_DATUM_TEXT_DE.finditer(zeile):
        monat = MONATE.get(m.group(2).lower())
        if monat and (d := _datum(int(m.group(3)), monat, int(m.group(1)))):
            gefunden.append((m.start(), d))
    for m in RE_DATUM_TEXT_EN.finditer(zeile):
        monat = MONATE.get(m.group(1).lower())
        if monat and (d := _datum(int(m.group(3)), monat, int(m.group(2)))):
            gefunden.append((m.start(), d))
    return [d for _, d in sorted(gefunden, key=lambda x: x[0])]


RE_TAG_MONAT = re.compile(r"\b\d{1,2}\.\s*(?:" + "|".join(sorted(MONATE, key=len, reverse=True)) + r")\b",
                          re.IGNORECASE)
RE_WAEHRUNG_NAH = re.compile(r"^\s?(?:€|EUR\b|USD\b|US\$|\$|£|GBP\b|CHF\b)|(?:€|EUR|USD|\$|£|GBP|CHF)\s?$",
                             re.IGNORECASE)


def _betraege(zeile: str) -> list[Decimal]:
    """Geldbeträge einer Zeile.

    Ganze Zahlen ohne Währungszeichen (Tage, Mengen, Kundennummern) zählen nicht,
    sonst wird aus "am 10. September abgebucht" ein Rechnungsbetrag von 10 €.
    """
    ohne_daten = RE_DATUM_DE.sub(" ", RE_DATUM_ISO.sub(" ", zeile))
    ohne_daten = RE_DATUM_TEXT_EN.sub(" ", RE_DATUM_TEXT_DE.sub(" ", ohne_daten))
    ohne_daten = RE_TAG_MONAT.sub(" ", ohne_daten)
    ohne_saetze = re.sub(r"\d+(?:[.,]\d+)?\s?%", " ", ohne_daten)
    werte = []
    for m in RE_BETRAG.finditer(ohne_saetze):
        text = m.group(1)
        mit_cent = re.search(r"[.,]\d{2}$", text)
        umfeld = ohne_saetze[max(0, m.start() - 4):m.start()], ohne_saetze[m.end():m.end() + 4]
        mit_waehrung = RE_WAEHRUNG_NAH.search(umfeld[0]) or RE_WAEHRUNG_NAH.search(umfeld[1])
        if not (mit_cent or mit_waehrung):
            continue
        if (w := betrag_parsen(text)) is not None:
            werte.append(w)
    return werte


def _beschriftung(zeile: str) -> str | None:
    if SCHLUESSEL_NETTO.search(zeile):
        return "netto"
    if SCHLUESSEL_BRUTTO.search(zeile):
        return "brutto"
    if (SCHLUESSEL_STEUER.search(zeile) and not SCHLUESSEL_KEINE_STEUER.search(zeile)
            and not RE_USTID.search(zeile)):
        return "steuer"
    if SCHLUESSEL_SUMME.search(zeile):
        return "summe"
    return None


def _nur_betrag(zeile: str) -> bool:
    """Zeile mit genau einem Betrag und höchstens einer Fußnote wie "A1"."""
    return len(_betraege(zeile)) == 1 and len(re.sub(r"[\d.,'€$£\s−-]|EUR|USD|CHF|GBP", "", zeile)) <= 3


def _tabellenzeile(werte: list[Decimal]) -> tuple[Decimal, Decimal, Decimal] | None:
    """Drei aufeinanderfolgende Beträge Netto, Steuer, Brutto mit Netto + Steuer = Brutto."""
    for i in range(len(werte) - 2):
        netto, steuer, brutto = werte[i:i + 3]
        if netto > 0 and steuer > 0 and netto + steuer == brutto and steuer <= netto * Decimal("0.2"):
            return netto, steuer, brutto
    return None


def steuer_passt(netto: Decimal, steuer: Decimal, satz: Decimal) -> bool:
    """Steuer ≈ Netto × Satz (Rundung je Position erlaubt ein paar Cent Abweichung)."""
    erwartet = netto * satz / 100
    return abs(steuer - erwartet) <= max(Decimal("0.05"), abs(erwartet) * Decimal("0.01"))


@dataclass
class Erkennung:
    art: Art | None = None
    steuerfall: Steuerfall | None = None
    rechnungsdatum: date | None = None
    netto: Decimal | None = None
    steuer: Decimal | None = None
    brutto: Decimal | None = None
    steuersatz: Decimal | None = None
    waehrung: str = "EUR"
    partner_ustid: str = ""
    rechnungsnummer: str = ""
    reverse_charge: bool = False
    drittland: bool = False
    partner_de_ustid: bool = False  # Anbieter nennt (auch) eine deutsche USt-IdNr.
    hinweise: list[str] = field(default_factory=list)


def ustids_finden(text: str, ausser: str = "") -> list[str]:
    ausser = ausser.replace(" ", "").upper()
    ids = []
    for m in RE_USTID.finditer(text):
        ustid = m.group(0).replace(" ", "")
        if ustid != ausser and ustid not in ids:
            ids.append(ustid)
    return ids


def erkennen(text: str, art: Art | None = None, eigene_ustid: str = "") -> Erkennung:
    e = Erkennung(art=art)
    if not text.strip():
        e.hinweise.append("Kein Text im Beleg gefunden (Scan?). Werte bitte manuell eintragen "
                          "oder den Beleg über Paperless mit Texterkennung einlesen.")
        return e

    zeilen = [z.strip() for z in text.splitlines() if z.strip()]

    # Währung: die erste, die in Betragszeilen vorkommt, sonst im ganzen Text
    for muster, code in WAEHRUNGEN:
        if muster.search(text):
            e.waehrung = code
            break

    # Datum: bevorzugt in einer Zeile mit Schlüsselwort
    for z in zeilen:
        if SCHLUESSEL_DATUM.search(z) and (d := daten_finden(z)):
            e.rechnungsdatum = d[0]
            break
    if not e.rechnungsdatum:
        for z in zeilen:
            if d := daten_finden(z):
                e.rechnungsdatum = d[0]
                break

    # Beträge aus beschrifteten Zeilen; die jeweils letzte Fundstelle gewinnt. Steht die Beschriftung
    # allein in einer Zeile (Tabellenzellen untereinander), gilt sie für den Betrag in der nächsten.
    vorige: str | None = None
    for z in zeilen:
        werte = _betraege(z)
        beschriftung = _beschriftung(z)
        if not werte:
            vorige = beschriftung if len(z) <= 30 else None
            continue
        if beschriftung is None and vorige and _nur_betrag(z):
            beschriftung = vorige
        vorige = None
        letzter = werte[-1]
        if beschriftung == "netto":
            e.netto = letzter
        elif beschriftung in ("brutto", "summe"):
            e.brutto = letzter
        elif beschriftung == "steuer":
            e.steuer = letzter
            if s := RE_SATZ.search(z):
                e.steuersatz = Decimal(s.group(1))

    # Tabellen wie "Summe 14,19 € 2,70 € 16,89 €" sind eindeutiger als einzelne Schlüsselwörter;
    # die Zeile mit dem größten Bruttobetrag ist die Gesamtsumme. Steht jede Zelle in einer eigenen
    # Zeile, bilden aufeinanderfolgende reine Betragszeilen die Tabellenzeile.
    folgen = [_betraege(z) for z in zeilen]
    lauf: list[Decimal] = []
    for z in zeilen:
        if _nur_betrag(z):
            lauf += _betraege(z)
        elif lauf:
            folgen.append(lauf)
            lauf = []
    folgen.append(lauf)
    tabelle = max((t for werte in folgen if (t := _tabellenzeile(werte))), key=lambda t: t[2], default=None)
    if tabelle:
        e.netto, e.steuer, e.brutto = tabelle

    if e.steuersatz is None:
        saetze = {Decimal(m.group(1)) for m in RE_SATZ.finditer(text)}
        if len(saetze) == 1:
            e.steuersatz = saetze.pop()
        elif Decimal(19) in saetze:
            e.steuersatz = Decimal(19)

    # fehlende Werte ergänzen
    if e.netto is None and e.brutto is not None:
        if e.steuer is not None:
            e.netto = e.brutto - e.steuer
        elif e.steuersatz:
            e.netto = (e.brutto * 100 / (100 + e.steuersatz)).quantize(Decimal("0.01"))
            e.hinweise.append("Nettobetrag aus Bruttobetrag und Steuersatz berechnet.")
        else:
            e.netto = e.brutto
    if e.steuer is None and e.netto is not None and e.brutto is not None:
        e.steuer = e.brutto - e.netto
    if e.steuersatz is None and e.netto and e.steuer is not None:
        quote = (e.steuer * 100 / e.netto).quantize(Decimal(1))
        if quote in (Decimal(19), Decimal(7), Decimal(0)):
            e.steuersatz = quote

    if e.netto is None:
        e.hinweise.append("Kein Betrag erkannt. Bitte Netto und Steuer manuell eintragen.")
    if e.rechnungsdatum is None:
        e.hinweise.append("Kein Rechnungsdatum erkannt.")

    if m := RE_RECHNUNGSNR.search(text):
        e.rechnungsnummer = m.group(1)

    ids = ustids_finden(text, eigene_ustid)
    fremde = [i for i in ids if not i.startswith("DE")]
    e.partner_ustid = (fremde or ids or [""])[0]
    e.partner_de_ustid = any(i.startswith("DE") for i in ids)
    e.reverse_charge = bool(RE_REVERSE_CHARGE.search(text))
    e.drittland = bool(RE_DRITTLAND.search(text)) and not fremde
    if RE_OSS.search(text):
        e.hinweise.append("Der Anbieter nutzt eine EU-OSS-Nummer (Sitz außerhalb der EU). Wurde deutsche "
                          "Umsatzsteuer berechnet, obwohl du Unternehmer bist, beim Anbieter die USt-IdNr. "
                          "hinterlegen.")

    e.steuerfall = steuerfall_vorschlagen(e)
    return e


HINWEIS_AUSL_STEUER = ("Ausländischer Anbieter mit ausgewiesener Steuer: Ist es deutsche Umsatzsteuer? "
                       "Nur dann ist sie als Vorsteuer abziehbar.")


def hinweis_eu_steuer(e: Erkennung) -> str:
    betrag = f" ({e.steuer:.2f} {e.waehrung})".replace(".", ",") if e.steuer else ""
    return (f"Anbieter aus dem EU-Ausland ({e.partner_ustid}) mit ausgewiesener Umsatzsteuer{betrag}, als "
            "Vorsteuer (Kz 66) vorgeschlagen. Streng genommen gilt für Leistungen an Unternehmer § 13b; mit "
            "deiner USt-IdNr. beim Anbieter kommen künftige Rechnungen ohne Steuer.")


def steuerfall_vorschlagen(e: Erkennung) -> Steuerfall | None:
    hat_steuer = bool(e.steuer) and e.steuer != 0
    eu_fremd = bool(e.partner_ustid) and not e.partner_ustid.startswith("DE")
    fremdwaehrung = e.waehrung != "EUR"

    if e.art == Art.EINGANG:
        if hat_steuer and not e.reverse_charge:
            # Marcels Entscheidung (10.10.2026): ausgewiesene Steuer eines EU-Anbieters wie bisher in sevDesk
            # als Vorsteuer buchen, nur mit Hinweis. Typisch, wenn beim Anbieter keine USt-IdNr. hinterlegt ist.
            if eu_fremd and not e.partner_de_ustid:
                hinweis = hinweis_eu_steuer(e)
            elif fremdwaehrung or e.drittland:
                hinweis = HINWEIS_AUSL_STEUER
            else:
                hinweis = ""
            if hinweis and hinweis not in e.hinweise:
                e.hinweise.append(hinweis)
            return Steuerfall.VORSTEUER_INLAND
        if eu_fremd:
            return Steuerfall.EU_LEISTUNG_13B
        if e.reverse_charge or fremdwaehrung or e.drittland:
            return Steuerfall.AUSLAND_13B
        return Steuerfall.OHNE_VORSTEUER

    if e.art == Art.AUSGANG:
        if not hat_steuer and (e.reverse_charge or eu_fremd):
            return Steuerfall.EU_LEISTUNG
        if not hat_steuer and (e.drittland or fremdwaehrung):
            return Steuerfall.DRITTLAND_LEISTUNG
        return Steuerfall.INLAND
    return None
