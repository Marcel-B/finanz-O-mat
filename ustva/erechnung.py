"""E-Rechnungen (EN 16931) strukturiert auslesen.

Unterstützt werden

- ZUGFeRD 2.x / Factur-X: PDF mit eingebetteter XML-Datei in der Syntax
  UN/CEFACT Cross Industry Invoice (CII),
- XRechnung und andere EN-16931-Rechnungen als reine XML-Datei, in CII- oder
  UBL-Syntax (``Invoice`` / ``CreditNote``).

Die Beträge stehen in der XML exakt, deshalb wird nichts geschätzt. Pro
Steuerkategorie und Steuersatz der Rechnung entsteht ein Beleg; eine Rechnung
mit 19 % und 7 % ergibt also zwei Belege.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pypdf import PdfReader

from .extraktion import EU_LAENDER
from .models import Beleg
from .steuerfaelle import Art, Steuerfall

NULL = Decimal(0)
GUTSCHRIFT_TYPEN = {"381", "261", "262", "296", "308", "396", "420", "458", "532"}
# Eingebettete Dateinamen laut ZUGFeRD 2.x, Factur-X und XRechnung
XML_NAMEN = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd_invoice.xml")


class ERechnungFehler(ValueError):
    pass


@dataclass
class Partei:
    name: str = ""
    ustid: str = ""
    land: str = ""

    @property
    def land_ustid(self) -> str:
        """Land aus der USt-IdNr. (Griechenland "EL"), sonst aus der Anschrift."""
        if len(self.ustid) >= 2 and self.ustid[:2].isalpha():
            return self.ustid[:2].upper()
        return "EL" if self.land == "GR" else self.land


@dataclass
class Steuerposten:
    kategorie: str  # UNTDID 5305: S, Z, E, AE, K, G, O, L, M
    satz: Decimal
    basis: Decimal
    steuer: Decimal
    befreiungsgrund: str = ""


@dataclass
class ERechnung:
    syntax: str  # CII oder UBL
    profil: str = ""  # XRechnung, EN 16931, Basic, Minimum, ...
    im_pdf: bool = False  # ZUGFeRD/Factur-X
    rechnungsnummer: str = ""
    rechnungsdatum: date | None = None
    waehrung: str = "EUR"
    gutschrift: bool = False
    verkaeufer: Partei = field(default_factory=Partei)
    kaeufer: Partei = field(default_factory=Partei)
    posten: list[Steuerposten] = field(default_factory=list)
    netto: Decimal | None = None
    steuer: Decimal | None = None
    zahlbetrag: Decimal | None = None

    @property
    def format(self) -> str:
        if self.im_pdf:
            return f"ZUGFeRD/Factur-X {self.profil}".strip()
        if self.profil == "XRechnung":
            return f"XRechnung ({self.syntax})"
        return f"E-Rechnung {self.profil} ({self.syntax})".replace("  ", " ")


# XML-Hilfen: Namensräume werden entfernt, gesucht wird mit lokalen Namen.

def _parsen(daten: bytes) -> ET.Element:
    kopf = daten[:4096]
    if b"<!DOCTYPE" in kopf or b"<!ENTITY" in daten:
        raise ERechnungFehler("XML mit DOCTYPE/ENTITY wird aus Sicherheitsgründen nicht gelesen")
    try:
        wurzel = ET.fromstring(daten)
    except ET.ParseError as e:
        raise ERechnungFehler(f"Keine gültige XML-Datei: {e}") from e
    for el in wurzel.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return wurzel


def _text(el: ET.Element | None, pfad: str = "") -> str:
    if el is None:
        return ""
    ziel = el.find(pfad) if pfad else el
    return (ziel.text or "").strip() if ziel is not None else ""


def _betrag(el: ET.Element | None, pfad: str = "", waehrung: str = "") -> Decimal | None:
    """Erster Betrag unter ``pfad``; bei mehreren (Rechnungs- und Steuerwährung) der in ``waehrung``."""
    if el is None:
        return None
    treffer = el.findall(pfad) if pfad else [el]
    if waehrung:
        passend = [t for t in treffer if t.get("currencyID", waehrung) == waehrung]
        treffer = passend or treffer
    for t in treffer:
        try:
            return Decimal((t.text or "").strip())
        except InvalidOperation:
            continue
    return None


def _datum(text: str) -> date | None:
    text = text.strip()
    try:
        if re.fullmatch(r"\d{8}", text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _profil(kennung: str) -> str:
    """Profil aus der Spezifikationskennung (BT-24)."""
    k = kennung.lower()
    if "xrechnung" in k:
        return "XRechnung"
    for name, teil in (("Minimum", "minimum"), ("Basic WL", "basicwl"), ("Basic", "basic"),
                       ("Extended", "extended")):
        if teil in k:
            return name
    return "EN 16931" if "en16931" in k else ""


# CII (ZUGFeRD, Factur-X, XRechnung CII)

def _cii_partei(p: ET.Element | None) -> Partei:
    if p is None:
        return Partei()
    ustid = ""
    for reg in p.findall("SpecifiedTaxRegistration/ID"):
        if reg.get("schemeID", "VA") == "VA":
            ustid = (reg.text or "").strip().replace(" ", "").upper()
            break
    return Partei(_text(p, "Name"), ustid, _text(p, "PostalTradeAddress/CountryID").upper())


def _cii(wurzel: ET.Element) -> ERechnung:
    if wurzel.find("SupplyChainTradeTransaction") is None:
        raise ERechnungFehler("CII-Struktur nicht erkannt (ZUGFeRD 1.0 wird nicht unterstützt)")
    doc = wurzel.find("ExchangedDocument")
    tx = wurzel.find("SupplyChainTradeTransaction")
    vereinbarung = tx.find("ApplicableHeaderTradeAgreement")
    abrechnung = tx.find("ApplicableHeaderTradeSettlement")
    waehrung = _text(abrechnung, "InvoiceCurrencyCode").upper() or "EUR"
    summen = abrechnung.find("SpecifiedTradeSettlementHeaderMonetarySummation") if abrechnung is not None else None

    r = ERechnung(
        syntax="CII",
        profil=_profil(_text(wurzel, "ExchangedDocumentContext/GuidelineSpecifiedDocumentContextParameter/ID")),
        rechnungsnummer=_text(doc, "ID"),
        rechnungsdatum=_datum(_text(doc, "IssueDateTime/DateTimeString")),
        waehrung=waehrung,
        gutschrift=_text(doc, "TypeCode") in GUTSCHRIFT_TYPEN,
        verkaeufer=_cii_partei(vereinbarung.find("SellerTradeParty") if vereinbarung is not None else None),
        kaeufer=_cii_partei(vereinbarung.find("BuyerTradeParty") if vereinbarung is not None else None),
        netto=_betrag(summen, "TaxBasisTotalAmount"),
        steuer=_betrag(summen, "TaxTotalAmount", waehrung),
        zahlbetrag=_betrag(summen, "DuePayableAmount") or _betrag(summen, "GrandTotalAmount"),
    )
    for t in abrechnung.findall("ApplicableTradeTax") if abrechnung is not None else []:
        if _text(t, "TypeCode") not in ("", "VAT"):
            continue
        r.posten.append(Steuerposten(
            kategorie=_text(t, "CategoryCode").upper() or "S",
            satz=_betrag(t, "RateApplicablePercent") or NULL,
            basis=_betrag(t, "BasisAmount") or NULL,
            steuer=_betrag(t, "CalculatedAmount") or NULL,
            befreiungsgrund=_text(t, "ExemptionReason"),
        ))
    return r


# UBL (XRechnung UBL, Peppol)

def _ubl_partei(p: ET.Element | None) -> Partei:
    if p is None:
        return Partei()
    ustid = ""
    for schema in p.findall("PartyTaxScheme"):
        if _text(schema, "TaxScheme/ID").upper() in ("VAT", ""):
            ustid = _text(schema, "CompanyID").replace(" ", "").upper()
            break
    name = _text(p, "PartyLegalEntity/RegistrationName") or _text(p, "PartyName/Name")
    return Partei(name, ustid, _text(p, "PostalAddress/Country/IdentificationCode").upper())


def _ubl(wurzel: ET.Element) -> ERechnung:
    gutschrift = wurzel.tag == "CreditNote"
    waehrung = _text(wurzel, "DocumentCurrencyCode").upper() or "EUR"
    typ = _text(wurzel, "CreditNoteTypeCode" if gutschrift else "InvoiceTypeCode")
    summen = wurzel.find("LegalMonetaryTotal")
    # Bei abweichender Steuerwährung gibt es zwei TaxTotal; die Aufschlüsselung steht in der Rechnungswährung.
    steuer_gesamt = None
    for tt in wurzel.findall("TaxTotal"):
        betrag = tt.find("TaxAmount")
        if betrag is not None and betrag.get("currencyID", waehrung) == waehrung:
            steuer_gesamt = tt
            break
    r = ERechnung(
        syntax="UBL",
        profil=_profil(_text(wurzel, "CustomizationID")),
        rechnungsnummer=_text(wurzel, "ID"),
        rechnungsdatum=_datum(_text(wurzel, "IssueDate")),
        waehrung=waehrung,
        gutschrift=gutschrift or typ in GUTSCHRIFT_TYPEN,
        verkaeufer=_ubl_partei(wurzel.find("AccountingSupplierParty/Party")),
        kaeufer=_ubl_partei(wurzel.find("AccountingCustomerParty/Party")),
        netto=_betrag(summen, "TaxExclusiveAmount"),
        steuer=_betrag(steuer_gesamt, "TaxAmount"),
        zahlbetrag=_betrag(summen, "PayableAmount"),
    )
    for st in steuer_gesamt.findall("TaxSubtotal") if steuer_gesamt is not None else []:
        kat = st.find("TaxCategory")
        if _text(kat, "TaxScheme/ID").upper() not in ("", "VAT"):
            continue
        r.posten.append(Steuerposten(
            kategorie=_text(kat, "ID").upper() or "S",
            satz=_betrag(kat, "Percent") or NULL,
            basis=_betrag(st, "TaxableAmount") or NULL,
            steuer=_betrag(st, "TaxAmount") or NULL,
            befreiungsgrund=_text(kat, "TaxExemptionReason"),
        ))
    return r


def xml_lesen(daten: bytes) -> ERechnung:
    wurzel = _parsen(daten)
    if wurzel.tag == "CrossIndustryInvoice":
        r = _cii(wurzel)
    elif wurzel.tag in ("Invoice", "CreditNote"):
        r = _ubl(wurzel)
    elif wurzel.tag == "CrossIndustryDocument":
        raise ERechnungFehler("ZUGFeRD 1.0 wird nicht unterstützt")
    else:
        raise ERechnungFehler(f"Keine E-Rechnung (Wurzelelement {wurzel.tag})")
    if not r.posten and r.netto is None:
        raise ERechnungFehler("E-Rechnung ohne Beträge")
    return r


def xml_aus_pdf(quelle: Path | bytes) -> bytes | None:
    """Liefert die eingebettete ZUGFeRD-/Factur-X-XML eines PDFs, sonst None."""
    try:
        leser = PdfReader(io.BytesIO(quelle) if isinstance(quelle, bytes) else quelle)
        anhaenge = leser.attachments
    except Exception:
        return None
    namen = sorted(anhaenge, key=lambda n: (n.lower() not in XML_NAMEN, n))
    for name in namen:
        if not name.lower().endswith(".xml"):
            continue
        for inhalt in anhaenge[name]:
            kopf = inhalt[:4096]
            if any(k in kopf for k in (b"CrossIndustryInvoice", b"CrossIndustryDocument", b"<Invoice", b":Invoice")):
                return inhalt
    return None


def lesen(pfad_oder_daten: Path | bytes, dateiname: str = "") -> ERechnung | None:
    """E-Rechnung aus einer PDF- oder XML-Datei lesen; None, wenn es keine ist.

    Wirft ERechnungFehler, wenn eine XML-Datei vorliegt, die sich nicht auslesen lässt.
    """
    if isinstance(pfad_oder_daten, Path):
        dateiname = dateiname or pfad_oder_daten.name
        daten = pfad_oder_daten.read_bytes()
    else:
        daten = pfad_oder_daten
    if daten[:5] == b"%PDF-":
        xml = xml_aus_pdf(daten)
        if not xml:
            return None
        r = xml_lesen(xml)
        r.im_pdf = True
        return r
    if dateiname.lower().endswith(".xml") or daten.lstrip()[:1] == b"<":
        return xml_lesen(daten)
    return None


# Abbildung auf Belege

def _art(r: ERechnung, art: Art, eigene_ustid: str, hinweise: list[str]) -> Art:
    eigene = eigene_ustid.replace(" ", "").upper()
    if eigene:
        if r.verkaeufer.ustid == eigene and art == Art.EINGANG:
            hinweise.append("Laut E-Rechnung bist du der Verkäufer, der Beleg liegt aber bei den Eingangsrechnungen.")
        if r.kaeufer.ustid == eigene and art == Art.AUSGANG:
            hinweise.append("Laut E-Rechnung bist du der Käufer, der Beleg liegt aber bei den Ausgangsrechnungen.")
    return art


def _steuerfall(p: Steuerposten, art: Art, partner: Partei, hinweise: list[str]) -> Steuerfall:
    land = partner.land_ustid
    eu_ausland = land in EU_LAENDER and land != "DE"
    drittland = bool(land) and land not in EU_LAENDER
    kat = p.kategorie

    if art == Art.AUSGANG:
        if kat == "S" or (kat not in ("AE", "K", "G", "O") and p.steuer):
            return Steuerfall.INLAND
        if kat == "K":
            return Steuerfall.EU_LIEFERUNG
        if kat == "G":
            return Steuerfall.AUSFUHR
        if kat == "AE" and eu_ausland:
            return Steuerfall.EU_LEISTUNG
        if kat in ("AE", "O") and drittland:
            return Steuerfall.DRITTLAND_LEISTUNG
        if kat == "AE":
            hinweise.append("Reverse Charge an einen Kunden im Inland (§ 13b) wird von der App nicht abgebildet.")
        else:
            hinweise.append(f"Steuerkategorie {kat} ({p.befreiungsgrund or 'steuerfrei'}) wird von der App nicht "
                            "abgebildet. Bitte Steuerfall prüfen.")
        return Steuerfall.IGNORIEREN

    # Eingangsrechnung
    if kat == "S" or (kat not in ("AE", "K") and p.steuer):
        if land and land != "DE":
            hinweise.append("Ausländische Umsatzsteuer ist nicht als Vorsteuer abziehbar; als Kosten mit "
                            "Steuer übernommen. Bitte prüfen, ob es doch deutsche Umsatzsteuer ist.")
            return Steuerfall.OHNE_VORSTEUER
        return Steuerfall.VORSTEUER_INLAND
    if kat == "K":
        return Steuerfall.EU_ERWERB
    if kat == "AE":
        return Steuerfall.EU_LEISTUNG_13B if eu_ausland else Steuerfall.AUSLAND_13B
    return Steuerfall.OHNE_VORSTEUER


REVERSE_CHARGE = {Steuerfall.EU_LEISTUNG_13B, Steuerfall.AUSLAND_13B, Steuerfall.EU_ERWERB}
OHNE_STEUER = {
    Steuerfall.EU_LEISTUNG, Steuerfall.EU_LIEFERUNG, Steuerfall.DRITTLAND_LEISTUNG, Steuerfall.AUSFUHR,
    Steuerfall.OHNE_VORSTEUER, Steuerfall.IGNORIEREN,
}


def belege(r: ERechnung, art: Art, eigene_ustid: str = "") -> list[Beleg]:
    """Ein Beleg je Steuerposten. ``pruefen`` ist nur gesetzt, wenn etwas unklar ist."""
    allgemein: list[str] = []
    art = _art(r, art, eigene_ustid, allgemein)
    partner = r.kaeufer if art == Art.AUSGANG else r.verkaeufer
    vorzeichen = Decimal(-1) if r.gutschrift else Decimal(1)

    posten = r.posten
    if not posten:  # Profil Minimum: nur Summen, keine Aufschlüsselung
        netto, steuer = r.netto or NULL, r.steuer or NULL
        satz = (steuer * 100 / netto).quantize(Decimal("1")) if netto else NULL
        posten = [Steuerposten("S" if steuer else "O", satz, netto, steuer)]
        allgemein.append("E-Rechnung ohne Steueraufschlüsselung (Profil Minimum): Steuerfall bitte prüfen.")
    elif r.netto is not None and sum((p.basis for p in posten), NULL) != r.netto:
        allgemein.append("Summe der Steuerposten weicht vom Nettobetrag der E-Rechnung ab.")
    if r.gutschrift:
        allgemein.append("Gutschrift bzw. Rechnungskorrektur: Beträge negativ übernommen.")

    ergebnis = []
    for p in posten:
        hinweise = list(allgemein)
        steuerfall = _steuerfall(p, art, partner, hinweise)
        satz, steuer, netto = p.satz, p.steuer * vorzeichen, p.basis * vorzeichen
        if steuerfall == Steuerfall.OHNE_VORSTEUER and p.steuer:
            netto += steuer  # nicht abziehbare (ausländische) Steuer ist Teil der Kosten
        if steuerfall in REVERSE_CHARGE:
            # Die Rechnung weist 0 % aus; die Steuer schuldest du selbst.
            if not satz:
                satz = Decimal(19)
                hinweise.append("Reverse Charge: Steuersatz 19 % angenommen.")
            steuer = None
        elif steuerfall in OHNE_STEUER:
            satz, steuer = NULL, NULL
        if r.waehrung != "EUR" and steuerfall == Steuerfall.VORSTEUER_INLAND:
            hinweise.append("Deutsche Umsatzsteuer in Fremdwährung: Umrechnung nach BMF-Monatskurs prüfen.")
        ergebnis.append(Beleg(
            art=art,
            steuerfall=steuerfall,
            rechnungsdatum=r.rechnungsdatum,
            netto=netto,
            steuersatz=satz,
            steuer=steuer,
            waehrung=r.waehrung,
            partner=partner.name,
            partner_ustid=partner.ustid,
            rechnungsnummer=r.rechnungsnummer,
            erechnung=r.format,
            # Reine Annahmen (19 % bei Reverse Charge) und Abweichungen sollen angesehen werden.
            pruefen=bool(hinweise) or r.rechnungsdatum is None,
            hinweise=hinweise,
        ))
    return ergebnis
