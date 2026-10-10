"""Umzug aus sevDesk: Komplettsicherung, Übernahme nach finanz-O-mat und Paperless, Abgleich.

1. ``sichern``: alle Daten über die sevDesk-API als JSON und alle Beleg- und Rechnungsdateien in
   einen Ordner. Der Ordner ist zugleich das Archiv für die Aufbewahrungspflicht und lässt sich
   nach einem Abbruch einfach erneut starten (vorhandene Dateien werden nicht noch einmal geladen).
2. ``importieren``: liest nur die Sicherung (sevDesk wird dafür nicht mehr gebraucht), lädt die
   Dateien nach Paperless und legt die Buchungen als geprüfte Belege an. Mit ``trocken=True``
   wird nichts geschrieben. Ein zweiter Lauf überspringt alles, was schon übernommen wurde.
3. ``monatsvergleich``: Zahllast je Monat aus finanz-O-mat gegen die abgegebenen Werte.

Steuerregeln und Status-Codes nach der sevDesk-API (sevdesk-Update 2.0, ältere Belege mit ``taxType``).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

import httpx

from .berechnung import berechnen
from .config import Config
from .db import Datenbank
from .extraktion import betrag_parsen
from .models import Beleg
from .quellen.paperless import PaperlessClient, PaperlessFehler, _fehlertext
from .steuerfaelle import Art, Steuerfall
from .waehrung import Umrechner

API_URL = "https://my.sevdesk.de/api/v1"
SEITE = 1000  # sevDesk liefert höchstens 1000 Einträge je Anfrage

# Was gesichert wird: Modell -> zusätzliche Parameter für die Liste
MODELLE: dict[str, dict[str, str]] = {
    "Contact": {"depth": "1"},  # depth=1: auch Personen, nicht nur Organisationen
    "ContactAddress": {},
    "CommunicationWay": {},
    "Voucher": {},
    "VoucherPos": {},
    "Invoice": {},
    "InvoicePos": {},
    "CreditNote": {},
    "CreditNotePos": {},
    "CheckAccount": {},
    "CheckAccountTransaction": {},
}

# Dateien zu Belegen (Ausgaben/Einnahmen), Rechnungen und Gutschriften
DATEI_PFADE = {
    "Voucher": ("Voucher/{id}/downloadDocument", {}),
    # preventSendBy: das Abrufen der PDF markiert die Rechnung nicht als versendet
    "Invoice": ("Invoice/{id}/getPdf", {"preventSendBy": "true"}),
    "CreditNote": ("CreditNote/{id}/getPdf", {"preventSendBy": "true"}),
}

EU_LAENDER = {
    "AT", "BE", "BG", "CY", "CZ", "DK", "EE", "EL", "GR", "ES", "FI", "FR", "HR", "HU", "IE", "IT", "LT", "LU",
    "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI",
}


class SevdeskFehler(Exception):
    pass


# --- Zugriff auf die sevDesk-API -------------------------------------------------------------


class SevdeskClient:
    def __init__(
        self, token: str, client: httpx.Client | None = None, url: str = API_URL,
        schlafen: Callable[[float], None] = time.sleep,
    ):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=120)
        self.client.headers.update({"Authorization": token, "Accept": "application/json"})
        self.schlafen = schlafen

    def _get(self, pfad: str, params: dict | None = None) -> httpx.Response:
        """GET mit Wiederholung bei Ratenbegrenzung (429), Serverfehlern und Netzproblemen."""
        for versuch in range(5):
            letzter = versuch == 4
            try:
                antwort = self.client.get(f"{self.url}/{pfad}", params=params)
            except httpx.HTTPError as e:
                if letzter:
                    raise SevdeskFehler(f"sevDesk nicht erreichbar: {e}") from e
                self.schlafen(2**versuch)
                continue
            if antwort.status_code in (401, 403):
                raise SevdeskFehler("sevDesk lehnt den API-Token ab (Einstellungen > Benutzer > API-Token)")
            if (antwort.status_code == 429 or antwort.status_code >= 500) and not letzter:
                warte = antwort.headers.get("retry-after", "")
                self.schlafen(float(warte) if warte.isdigit() else 2**versuch)
                continue
            return antwort
        raise AssertionError("unerreichbar")

    def alle(self, modell: str, params: dict | None = None) -> list[dict]:
        """Alle Einträge eines Modells, seitenweise.

        Es wird weitergeblättert, bis keine neuen Einträge mehr kommen: so geht nichts verloren,
        falls sevDesk weniger als ``SEITE`` Einträge je Seite liefert.
        """
        ergebnisse: list[dict] = []
        gesehen: set[str] = set()
        offset = 0
        while True:
            antwort = self._get(modell, {**(params or {}), "limit": SEITE, "offset": offset})
            if antwort.status_code != 200:
                raise SevdeskFehler(f"{modell}: sevDesk antwortet mit Fehler {antwort.status_code}")
            seite = antwort.json().get("objects") or []
            neu = [o for o in seite if str(o.get("id")) not in gesehen]
            if not neu:
                return ergebnisse
            ergebnisse.extend(neu)
            gesehen.update(str(o.get("id")) for o in neu)
            offset += len(seite)

    def datei(self, modell: str, objekt_id: str) -> tuple[str, bytes] | None:
        """Dateiname und Inhalt der Datei zu einem Beleg, einer Rechnung oder Gutschrift; None ohne Datei."""
        pfad, params = DATEI_PFADE[modell]
        antwort = self._get(pfad.format(id=objekt_id), params)
        if antwort.status_code in (400, 404):
            return None
        if antwort.status_code != 200:
            raise SevdeskFehler(f"{modell} {objekt_id}: Datei nicht abrufbar (Fehler {antwort.status_code})")
        if "json" not in antwort.headers.get("content-type", ""):
            return f"{modell}-{objekt_id}.pdf", antwort.content
        daten = antwort.json()
        meta = daten.get("objects", daten) if isinstance(daten, dict) else None
        if isinstance(meta, list):
            meta = meta[0] if meta else None
        if not isinstance(meta, dict) or not meta.get("content"):
            return None
        inhalt = meta["content"]
        b64 = meta.get("base64Encoded", meta.get("base64encoded", True))
        roh = base64.b64decode(inhalt) if b64 not in (False, "false", 0, "0") else str(inhalt).encode("latin-1")
        name = Path(str(meta.get("filename") or f"{modell}-{objekt_id}.pdf")).name
        return name, roh


# --- Schritt 1: Sicherung --------------------------------------------------------------------


def _json_schreiben(pfad: Path, daten) -> None:
    tmp = pfad.with_suffix(pfad.suffix + ".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(pfad)


def sichern(
    client: SevdeskClient, ordner: Path, protokoll: Callable[[str], None] = lambda _: None
) -> dict:
    """Sichert alle Daten und Dateien aus sevDesk nach ``ordner`` und gibt die Übersicht zurück."""
    ordner.mkdir(parents=True, exist_ok=True)
    uebersicht_pfad = ordner / "sicherung.json"
    alt = json.loads(uebersicht_pfad.read_text(encoding="utf-8")) if uebersicht_pfad.exists() else {}
    dateien: dict[str, dict] = alt.get("dateien", {})
    fehler: list[str] = []
    daten: dict[str, list[dict]] = {}

    for modell, params in MODELLE.items():
        try:
            daten[modell] = client.alle(modell, params)
        except SevdeskFehler as e:
            if "API-Token" in str(e):
                raise
            fehler.append(str(e))
            daten[modell] = []
        _json_schreiben(ordner / f"{modell}.json", daten[modell])
        protokoll(f"{modell}: {len(daten[modell])}")

    uebersicht = {
        "erstellt": datetime.now().isoformat(timespec="seconds"),
        "quelle": client.url,
        "anzahl": {m: len(v) for m, v in daten.items()},
        "dateien": dateien,
        "ohne_datei": [],
        "fehler": fehler,
    }
    zu_laden = [
        (modell, o) for modell in DATEI_PFADE for o in daten[modell]
        # Belege: nur mit Datei; Rechnungen und Gutschriften: keine Entwürfe
        if (o.get("document") if modell == "Voucher" else str(o.get("status")) not in ("50", "100"))
    ]
    for i, (modell, o) in enumerate(zu_laden, 1):
        schluessel = f"{modell}/{o['id']}"
        if schluessel in dateien and (ordner / dateien[schluessel]["datei"]).exists():
            continue
        try:
            ergebnis = client.datei(modell, str(o["id"]))
        except SevdeskFehler as e:
            fehler.append(str(e))
            continue
        if ergebnis is None:
            uebersicht["ohne_datei"].append(schluessel)
            continue
        name, inhalt = ergebnis
        rel = f"dateien/{modell}/{o['id']}{Path(name).suffix.lower() or '.pdf'}"
        (ordner / rel).parent.mkdir(parents=True, exist_ok=True)
        (ordner / rel).write_bytes(inhalt)
        dateien[schluessel] = {"datei": rel, "name": name, "sha256": hashlib.sha256(inhalt).hexdigest()}
        if i % 25 == 0:
            protokoll(f"Dateien: {i} von {len(zu_laden)}")
            _json_schreiben(uebersicht_pfad, uebersicht)  # Zwischenstand, falls der Lauf abbricht
    _json_schreiben(uebersicht_pfad, uebersicht)
    kontakte_schreiben(daten, ordner / "kontakte.csv")
    return uebersicht


def _kontakt_name(k: dict) -> str:
    if k.get("name"):
        return str(k["name"]).strip()
    return " ".join(str(k.get(f) or "").strip() for f in ("surename", "familyname")).strip()


def _ref_id(wert) -> str:
    """ID aus einem Verweis wie {"id": "12", "objectName": "Contact"}."""
    return str(wert.get("id")) if isinstance(wert, dict) and wert.get("id") is not None else ""


def kontakte_schreiben(daten: dict[str, list[dict]], pfad: Path) -> None:
    """Kunden und Lieferanten als CSV (Semikolon, UTF-8 mit BOM für Excel)."""
    adressen: dict[str, dict] = {}
    for a in daten.get("ContactAddress", []):
        adressen.setdefault(_ref_id(a.get("contact")), a)
    wege: dict[str, dict[str, str]] = {}
    for w in daten.get("CommunicationWay", []):
        art = str(w.get("type") or "").upper()
        wege.setdefault(_ref_id(w.get("contact")), {}).setdefault(art, str(w.get("value") or ""))
    felder = ["Kundennummer", "Name", "USt-IdNr", "Steuernummer", "Straße", "PLZ", "Ort", "E-Mail", "Telefon",
              "IBAN", "Beschreibung", "sevDesk-ID"]
    with pfad.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(felder)
        for k in daten.get("Contact", []):
            kid = str(k.get("id"))
            a = adressen.get(kid, {})
            kontakt = wege.get(kid, {})
            w.writerow([
                k.get("customerNumber") or "", _kontakt_name(k), k.get("vatNumber") or "", k.get("taxNumber") or "",
                a.get("street") or "", a.get("zip") or "", a.get("city") or "", kontakt.get("EMAIL", ""),
                kontakt.get("PHONE", "") or kontakt.get("MOBILE", ""), k.get("bankAccount") or "",
                k.get("description") or "", kid,
            ])


@dataclass
class Sicherung:
    ordner: Path
    daten: dict[str, list[dict]]
    dateien: dict[str, dict]
    erstellt: str = ""

    @classmethod
    def laden(cls, ordner: Path) -> Sicherung:
        uebersicht_pfad = ordner / "sicherung.json"
        if not uebersicht_pfad.exists():
            raise SevdeskFehler(f"In {ordner} liegt keine sevDesk-Sicherung (sicherung.json fehlt)")
        uebersicht = json.loads(uebersicht_pfad.read_text(encoding="utf-8"))
        daten = {}
        for modell in MODELLE:
            pfad = ordner / f"{modell}.json"
            daten[modell] = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else []
        return cls(ordner, daten, uebersicht.get("dateien", {}), uebersicht.get("erstellt", ""))

    def kandidaten(self) -> list[Kandidat]:
        return kandidaten(self)

    def datei(self, modell: str, objekt_id: str) -> Path | None:
        eintrag = self.dateien.get(f"{modell}/{objekt_id}")
        if not eintrag:
            return None
        pfad = self.ordner / eintrag["datei"]
        return pfad if pfad.exists() else None


# --- Schritt 2: Übernahme --------------------------------------------------------------------


@dataclass(frozen=True)
class Regel:
    titel: str
    art: Art
    steuerfall: Steuerfall
    hinweis: str = ""  # gesetzt: Abbildung unsicher, Beleg bekommt diesen Hinweis


NICHT_ABGEBILDET = "Bitte prüfen: in finanz-O-mat nicht abgebildet, zählt nicht für die UStVA."
OSS = "One-Stop-Shop-Umsätze gehören in die OSS-Erklärung, nicht in die UStVA."

# sevDesk-Steuerregeln (sevdesk-Update 2.0)
STEUERREGELN: dict[str, Regel] = {
    "1": Regel("Umsatzsteuerpflichtige Umsätze", Art.AUSGANG, Steuerfall.INLAND),
    "2": Regel("Ausfuhren", Art.AUSGANG, Steuerfall.AUSFUHR),
    "3": Regel("Innergemeinschaftliche Lieferungen", Art.AUSGANG, Steuerfall.EU_LIEFERUNG),
    "4": Regel("Steuerfreie Umsätze § 4 UStG", Art.AUSGANG, Steuerfall.IGNORIEREN,
               f"sevDesk: steuerfreier Umsatz nach § 4 UStG (Kz 48). {NICHT_ABGEBILDET}"),
    "5": Regel("Reverse Charge § 13b UStG (Kz 60)", Art.AUSGANG, Steuerfall.IGNORIEREN,
               f"sevDesk: Umsatz nach § 13b, Steuer schuldet der Kunde (Kz 60). {NICHT_ABGEBILDET}"),
    "11": Regel("Kleinunternehmer § 19 UStG", Art.AUSGANG, Steuerfall.IGNORIEREN,
                f"sevDesk: Kleinunternehmer nach § 19 UStG. {NICHT_ABGEBILDET}"),
    "17": Regel("Nicht im Inland steuerbare Leistung", Art.AUSGANG, Steuerfall.DRITTLAND_LEISTUNG),
    "18": Regel("One-Stop-Shop (Waren)", Art.AUSGANG, Steuerfall.IGNORIEREN, OSS),
    "19": Regel("One-Stop-Shop (elektronische Leistungen)", Art.AUSGANG, Steuerfall.IGNORIEREN, OSS),
    "20": Regel("One-Stop-Shop (sonstige Leistungen)", Art.AUSGANG, Steuerfall.IGNORIEREN, OSS),
    "21": Regel("Reverse Charge § 18b UStG (Kz 21)", Art.AUSGANG, Steuerfall.EU_LEISTUNG),
    "22": Regel("Nicht steuerbar (Einnahme)", Art.AUSGANG, Steuerfall.IGNORIEREN),
    "8": Regel("Innergemeinschaftliche Erwerbe", Art.EINGANG, Steuerfall.EU_ERWERB),
    "9": Regel("Vorsteuerabziehbare Aufwendungen", Art.EINGANG, Steuerfall.VORSTEUER_INLAND),
    "10": Regel("Nicht vorsteuerabziehbare Aufwendungen", Art.EINGANG, Steuerfall.OHNE_VORSTEUER),
    "12": Regel("Reverse Charge § 13b Abs. 2 mit Vorsteuerabzug", Art.EINGANG, Steuerfall.AUSLAND_13B),
    "13": Regel("Reverse Charge § 13b ohne Vorsteuerabzug", Art.EINGANG, Steuerfall.AUSLAND_13B,
                "sevDesk: Reverse Charge ohne Vorsteuerabzug. finanz-O-mat zieht die Steuer als Vorsteuer "
                "(Kz 67) wieder ab; bitte prüfen."),
    "14": Regel("Reverse Charge § 13b Abs. 1 (EU)", Art.EINGANG, Steuerfall.EU_LEISTUNG_13B),
    "16": Regel("Nicht steuerbar (Ausgabe)", Art.EINGANG, Steuerfall.OHNE_VORSTEUER),
}

# Ältere Belege (vor sevdesk-Update 2.0) haben statt der Steuerregel einen taxType
STEUERTYPEN: dict[tuple[Art, str], Regel] = {
    (Art.AUSGANG, "default"): STEUERREGELN["1"],
    (Art.AUSGANG, "eu"): STEUERREGELN["3"],
    (Art.AUSGANG, "noteu"): STEUERREGELN["17"],
    (Art.AUSGANG, "ss"): STEUERREGELN["11"],
    (Art.EINGANG, "default"): STEUERREGELN["9"],
    (Art.EINGANG, "eu"): STEUERREGELN["8"],
    (Art.EINGANG, "ss"): STEUERREGELN["10"],
}

RC_EINGANG = {Steuerfall.EU_LEISTUNG_13B, Steuerfall.AUSLAND_13B, Steuerfall.EU_ERWERB}
OHNE_STEUER = {
    Steuerfall.AUSFUHR, Steuerfall.EU_LIEFERUNG, Steuerfall.EU_LEISTUNG, Steuerfall.DRITTLAND_LEISTUNG,
    Steuerfall.OHNE_VORSTEUER,
}


@dataclass
class Kandidat:
    """Ein sevDesk-Beleg bzw. eine Rechnung mit den Belegen, die daraus in finanz-O-mat entstehen."""

    objekt: str  # Voucher | Invoice | CreditNote
    sevdesk_id: str
    titel: str
    belege: list[Beleg] = field(default_factory=list)
    datei: Path | None = None
    grund: str = ""  # gesetzt: wird nicht übernommen

    @property
    def monat(self) -> str:
        datum = self.belege[0].rechnungsdatum if self.belege else None
        return f"{datum:%Y-%m}" if datum else ""


def _datum(wert) -> date | None:
    """sevDesk liefert "2024-03-15T00:00:00+01:00", "15.03.2024" oder Unix-Zeitstempel."""
    if wert in (None, ""):
        return None
    s = str(wert).strip()
    if re.fullmatch(r"\d{9,10}", s):
        return datetime.fromtimestamp(int(s)).date()
    if m := re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s):
        return date(int(m[3]), int(m[2]), int(m[1]))
    try:
        return date.fromisoformat(s[:10])  # Datumsteil wörtlich nehmen, keine Zeitzonen-Umrechnung
    except ValueError:
        return None


def _dec(*werte) -> Decimal | None:
    for wert in werte:
        if wert in (None, ""):
            continue
        try:
            return Decimal(str(wert))
        except InvalidOperation:
            continue
    return None


def _satz(wert) -> Decimal:
    satz = _dec(wert) or Decimal(0)
    return satz.quantize(Decimal(1)) if satz == satz.to_integral_value() else satz.normalize()


def _regel(obj: dict, art: Art | None) -> tuple[Regel | None, str]:
    """Steuerregel des Belegs; zweiter Wert: Text für den Hinweis, wenn keine passt."""
    regel_id = _ref_id(obj.get("taxRule"))
    if regel_id:
        return STEUERREGELN.get(regel_id), f"Steuerregel {regel_id}"
    typ = str(obj.get("taxType") or "")
    if art is None:
        return None, f"taxType {typ or 'leer'}"
    return STEUERTYPEN.get((art, typ)), f"taxType {typ or 'leer'}"


def _summen(positionen: list[dict], kopf: dict) -> dict[Decimal, tuple[Decimal, Decimal | None]]:
    """Netto und Steuer je Steuersatz in EUR (Buchungswerte von sevDesk).

    Bei nur einem Steuersatz gelten die Summen des Belegs (dort sind Rabatte schon abgezogen).
    """
    kopf_netto = _dec(kopf.get("sumNetAccounting"), kopf.get("sumNet"))
    kopf_steuer = _dec(kopf.get("sumTaxAccounting"), kopf.get("sumTax"))
    gruppen: dict[Decimal, list[Decimal | None]] = {}
    for p in positionen:
        netto = _dec(p.get("sumNetAccounting"), p.get("sumNet"))
        if netto is None and (preis := _dec(p.get("priceNet"), p.get("price"))) is not None:
            netto = preis * (_dec(p.get("quantity")) or Decimal(1))
        steuer = _dec(p.get("sumTaxAccounting"), p.get("sumTax"))
        if steuer is None and netto is not None and (brutto := _dec(p.get("sumGrossAccounting"), p.get("sumGross"))):
            steuer = brutto - netto
        g = gruppen.setdefault(_satz(p.get("taxRate")), [Decimal(0), None])
        g[0] += netto or Decimal(0)
        if steuer is not None:
            g[1] = (g[1] or Decimal(0)) + steuer
    if len(gruppen) <= 1 and kopf_netto is not None:
        if gruppen:
            satz = next(iter(gruppen))
        elif kopf_netto and kopf_steuer:
            satz = min((Decimal(19), Decimal(7)), key=lambda s: abs(kopf_steuer / kopf_netto * 100 - s))
        else:
            satz = Decimal(0)
        return {satz: (kopf_netto, kopf_steuer)}
    return {s: (n, st) for s, (n, st) in gruppen.items()}


def _belege(
    regel: Regel, art: Art, summen: dict[Decimal, tuple[Decimal, Decimal | None]], **felder
) -> list[Beleg]:
    belege = []
    for satz, (netto, steuer) in sorted(summen.items(), key=lambda s: -s[0]):
        fall = regel.steuerfall
        hinweise = [regel.hinweis] if regel.hinweis else []
        if fall == Steuerfall.VORSTEUER_INLAND and satz == 0:
            fall = Steuerfall.OHNE_VORSTEUER
        elif fall == Steuerfall.INLAND and satz == 0:
            fall = Steuerfall.IGNORIEREN
            hinweise.append(f"sevDesk: steuerpflichtiger Umsatz mit 0 %. {NICHT_ABGEBILDET}")
        if fall in RC_EINGANG:
            # Die Steuer berechnet man selbst; sevDesk führt diese Belege mit 0 %.
            satz = satz or Decimal(19)
            steuer = steuer if steuer and steuer > 0 else None
        elif fall in OHNE_STEUER:
            satz, steuer = Decimal(0), Decimal(0)
        belege.append(Beleg(
            art=art, steuerfall=fall, netto=netto, steuersatz=satz, steuer=steuer, hinweise=hinweise, **felder,
        ))
    return belege


def _nach_beleg(positionen: list[dict], schluessel: str) -> dict[str, list[dict]]:
    ergebnis: dict[str, list[dict]] = {}
    for p in positionen:
        ergebnis.setdefault(_ref_id(p.get(schluessel)), []).append(p)
    return ergebnis


def _kandidat_voucher(v: dict, positionen: list[dict], kontakte: dict[str, dict], s: Sicherung) -> Kandidat:
    vid = str(v["id"])
    kontakt = kontakte.get(_ref_id(v.get("supplier")), {})
    partner = str(v.get("supplierNameAtSave") or v.get("supplierName") or _kontakt_name(kontakt))
    nummer = str(v.get("description") or "")
    k = Kandidat("Voucher", vid, " ".join(t for t in (f"Beleg {nummer or vid}", partner) if t),
                 datei=s.datei("Voucher", vid))
    if str(v.get("status")) == "50":
        k.grund = "Entwurf"
        return k
    if str(v.get("voucherType") or "VOU") == "RV":
        k.grund = "Vorlage für wiederkehrende Belege"
        return k
    seite = str(v.get("creditDebit") or "").upper()
    art_laut_seite = {"C": Art.EINGANG, "D": Art.AUSGANG}.get(seite)
    regel, regel_text = _regel(v, art_laut_seite)
    art = regel.art if regel else art_laut_seite
    if art is None:
        k.grund = f"Weder Steuerregel noch Ausgabe/Einnahme erkennbar ({regel_text})"
        return k
    if regel is None:
        if v.get("taxType") == "noteu" and art == Art.EINGANG:
            land = str(kontakt.get("vatNumber") or "")[:2].upper()
            regel = STEUERREGELN["14" if land in EU_LAENDER else "12"]
        else:
            fall = Steuerfall.INLAND if art == Art.AUSGANG else Steuerfall.VORSTEUER_INLAND
            regel = Regel(regel_text, art, fall, f"sevDesk: unbekannte Steuerregel ({regel_text}); bitte prüfen.")
    texte = [str(p.get("comment") or "").strip() for p in positionen]
    k.belege = _belege(
        regel, art, _summen(positionen, v),
        rechnungsdatum=_datum(v.get("voucherDate")), zahlungsdatum=_datum(v.get("payDate")),
        partner=partner, partner_ustid=str(kontakt.get("vatNumber") or "").replace(" ", "").upper(),
        rechnungsnummer=nummer, beschreibung=", ".join(dict.fromkeys(t for t in texte if t)) or f"sevDesk {nummer}",
    )
    _gemeinsame_hinweise(k, v)
    return k


def _kandidat_invoice(r: dict, positionen: list[dict], kontakte: dict[str, dict], s: Sicherung) -> Kandidat:
    rid = str(r["id"])
    kontakt = kontakte.get(_ref_id(r.get("contact")), {})
    partner = str(r.get("addressName") or _kontakt_name(kontakt))
    nummer = str(r.get("invoiceNumber") or "")
    k = Kandidat("Invoice", rid, " ".join(t for t in (f"Rechnung {nummer or rid}", partner) if t),
                 datei=s.datei("Invoice", rid))
    typ = str(r.get("invoiceType") or "RE").upper()
    if str(r.get("status")) in ("50", "100"):
        k.grund = "Entwurf"
        return k
    if typ in ("WKR", "MA"):
        k.grund = "Vorlage für wiederkehrende Rechnungen" if typ == "WKR" else "Mahnung"
        return k
    regel, regel_text = _regel(r, Art.AUSGANG)
    if regel is None or regel.art != Art.AUSGANG:
        regel = Regel(regel_text, Art.AUSGANG, Steuerfall.INLAND,
                      f"sevDesk: unbekannte Steuerregel ({regel_text}); bitte prüfen.")
    summen = _summen(positionen, r)
    if typ == "SR" and sum(n for n, _ in summen.values()) > 0:  # Stornorechnung mindert den Umsatz
        summen = {satz: (-n, None if st is None else -st) for satz, (n, st) in summen.items()}
    k.belege = _belege(
        regel, Art.AUSGANG, summen,
        rechnungsdatum=_datum(r.get("invoiceDate")), zahlungsdatum=_datum(r.get("payDate")),
        partner=partner, partner_ustid=str(kontakt.get("vatNumber") or "").replace(" ", "").upper(),
        rechnungsnummer=nummer, beschreibung=f"sevDesk {'Stornorechnung' if typ == 'SR' else 'Rechnung'} {nummer}",
    )
    _gemeinsame_hinweise(k, r)
    return k


def _gemeinsame_hinweise(k: Kandidat, obj: dict) -> None:
    hinweise = []
    if str(obj.get("status")) == "750":
        hinweise.append("sevDesk: nur teilweise bezahlt; Zahlungsdatum bitte prüfen.")
    waehrung = str(obj.get("currency") or "EUR").upper()
    if waehrung != "EUR":
        kurs = obj.get("propertyExchangeRate")
        hinweise.append(f"Original in {waehrung}" + (f" (Kurs {kurs})" if kurs else "")
                        + "; Beträge in EUR wie in sevDesk gebucht.")
    for b in k.belege:
        b.hinweise.extend(hinweise)


def kandidaten(s: Sicherung) -> list[Kandidat]:
    kontakte = {str(c.get("id")): c for c in s.daten.get("Contact", [])}
    voucher_pos = _nach_beleg(s.daten.get("VoucherPos", []), "voucher")
    invoice_pos = _nach_beleg(s.daten.get("InvoicePos", []), "invoice")
    ergebnis = [_kandidat_voucher(v, voucher_pos.get(str(v["id"]), []), kontakte, s)
                for v in s.daten.get("Voucher", [])]
    ergebnis += [_kandidat_invoice(r, invoice_pos.get(str(r["id"]), []), kontakte, s)
                 for r in s.daten.get("Invoice", [])]
    for k in ergebnis:
        if not k.grund and not k.belege:
            k.grund = "Ohne Beträge"
    for g in s.daten.get("CreditNote", []):
        nummer = g.get("creditNoteNumber") or g.get("id")
        entwurf = str(g.get("status")) in ("50", "100")
        ergebnis.append(Kandidat("CreditNote", str(g["id"]), f"Gutschrift {nummer}",
                                 grund="Entwurf" if entwurf else "Gutschrift (bitte von Hand übernehmen)"))
    return ergebnis


class PaperlessAblage:
    """Lädt Dateien nach Paperless und legt fehlende Tags und Korrespondenten an."""

    def __init__(self, pl: PaperlessClient, cfg: Config):
        self.pl = pl
        self.cfg = cfg
        self.tags = {n.lower(): i for i, n in pl.namen("/api/tags/").items()}
        self.korrespondenten = {n.lower(): i for i, n in pl.namen("/api/correspondents/").items()}

    def _post(self, pfad: str, **kwargs) -> httpx.Response:
        try:
            antwort = self.pl.client.post(f"{self.pl.url}{pfad}", **kwargs)
            antwort.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise PaperlessFehler(_fehlertext(e.response)) from e
        except httpx.HTTPError as e:
            raise PaperlessFehler(f"Paperless nicht erreichbar: {e}") from e
        return antwort

    def _id(self, liste: dict[str, int], pfad: str, name: str) -> int:
        if name.lower() not in liste:
            liste[name.lower()] = self._post(pfad, json={"name": name}).json()["id"]
        return liste[name.lower()]

    def hochladen(self, k: Kandidat) -> str:
        """Startet die Verarbeitung in Paperless und gibt die Aufgaben-ID zurück."""
        b = k.belege[0]
        tag_art = (self.cfg.paperless_tags_ausgang if b.art == Art.AUSGANG else self.cfg.paperless_tags_eingang)[0]
        daten: dict = {
            "title": k.titel,
            "tags": [str(self._id(self.tags, "/api/tags/", t)) for t in (tag_art, "sevDesk")],
        }
        if b.rechnungsdatum:
            daten["created"] = b.rechnungsdatum.isoformat()
        if b.partner:
            daten["correspondent"] = str(self._id(self.korrespondenten, "/api/correspondents/", b.partner))
        assert k.datei is not None
        dateien = {"document": (k.datei.name, k.datei.read_bytes())}
        antwort = self._post("/api/documents/post_document/", data=daten, files=dateien)
        return str(antwort.json()).strip('"')

    def aufgabe(self, task_id: str) -> tuple[str, str]:
        """("fertig" | "doppelt", Dokument-ID), ("wartet", "") oder ("fehler", Text)."""
        try:
            antwort = self.pl._get(f"{self.pl.url}/api/tasks/", params={"task_id": task_id})
            antwort.raise_for_status()
        except httpx.HTTPError as e:
            raise PaperlessFehler(f"Paperless-Aufgabe {task_id} nicht abrufbar: {e}") from e
        daten = antwort.json()
        liste = daten.get("results", []) if isinstance(daten, dict) else daten
        if not liste:
            return "wartet", ""
        t = liste[0]
        status = str(t.get("status") or "").upper()
        text = str(t.get("result") or "")
        if status == "SUCCESS":
            dok = t.get("related_document") or (m[1] if (m := re.search(r"id (\d+)", text)) else "")
            return ("fertig", str(dok)) if dok else ("fehler", text or "Paperless meldet keine Dokument-ID")
        if status in ("FAILURE", "REVOKED"):
            if "duplicate" in text.lower() and (m := re.search(r"#(\d+)", text)):
                return "doppelt", m[1]
            return "fehler", text or status
        return "wartet", ""


@dataclass
class Uebernahme:
    neu: int = 0
    vorhanden: int = 0
    ausserhalb: int = 0
    hochgeladen: int = 0
    wartet: int = 0
    belege: list[Beleg] = field(default_factory=list)
    uebersprungen: Counter = field(default_factory=Counter)
    doppelt: list[str] = field(default_factory=list)
    pruefen: list[str] = field(default_factory=list)
    ohne_datei: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)


def _anlegen(db: Datenbank, cfg: Config, k: Kandidat, dok_id: str, task: str, erg: Uebernahme) -> None:
    refs = [dok_id if i == 0 else f"{dok_id}#{i + 1}" for i in range(len(k.belege))] if dok_id else []
    # Hängt dieselbe Datei an mehreren sevDesk-Belegen, bekommt nur der erste die Paperless-Referenz.
    paperless = bool(refs) and not any(db.beleg_vorhanden("paperless", r) for r in refs)
    for i, b in enumerate(k.belege):
        if paperless:
            b.quelle, b.quelle_ref = "paperless", refs[i]
        else:
            b.quelle = "sevdesk"
            b.quelle_ref = f"{k.objekt}/{k.sevdesk_id}" + (f"#{i + 1}" if i else "")
        if dok_id:
            b.quelle_link = f"{cfg.paperless_url}/documents/{dok_id}/details"
        db.beleg_anlegen(b)
    db.sevdesk_import_speichern(k.objekt, k.sevdesk_id, "uebernommen", task, dok_id,
                                [b.id for b in k.belege if b.id is not None])
    erg.neu += 1
    erg.belege.extend(k.belege)


class Quelle(Protocol):
    """Sicherung über die API oder Export-Dateien der Oberfläche (sevdesk_export.Export)."""

    erstellt: str

    def kandidaten(self) -> list[Kandidat]: ...


def importieren(
    db: Datenbank, cfg: Config, sicherung: Quelle, *, trocken: bool = False, paperless_nutzen: bool = True,
    von: str | None = None, bis: str | None = None, warten: float = 300, client: httpx.Client | None = None,
    schlafen: Callable[[float], None] = time.sleep, uhr: Callable[[], float] = time.monotonic,
) -> Uebernahme:
    """Übernimmt die Belege aus Sicherung oder Export; ``von``/``bis`` als JJJJ-MM (Rechnungsdatum)."""
    erg = Uebernahme()
    bekannt = db.sevdesk_importe()
    vorhandene = {
        (b.art, b.rechnungsnummer.strip().lower(), b.rechnungsdatum)
        for b in db.alle_belege() if b.rechnungsnummer.strip()
    }
    ablage: PaperlessAblage | None = None
    if paperless_nutzen and not trocken:
        if not cfg.paperless_aktiv:
            erg.fehler.append("Paperless ist nicht eingerichtet (URL und Token); oder ohne Paperless übernehmen")
            return erg
        try:
            ablage = PaperlessAblage(PaperlessClient(cfg.paperless_url, cfg.paperless_token, client), cfg)
        except PaperlessFehler as e:
            erg.fehler.append(str(e))
            return erg

    offen: list[tuple[Kandidat, str]] = []
    for k in sicherung.kandidaten():
        if k.grund:
            erg.uebersprungen[k.grund] += 1
            continue
        if (von and k.monat < von) or (bis and k.monat > bis):
            erg.ausserhalb += 1
            continue
        eintrag = bekannt.get((k.objekt, k.sevdesk_id))
        if eintrag and eintrag["status"] in ("uebernommen", "doppelt"):
            erg.vorhanden += 1
            continue
        if not eintrag and any(
            (b.art, b.rechnungsnummer.strip().lower(), b.rechnungsdatum) in vorhandene for b in k.belege
        ):
            erg.doppelt.append(f"{k.titel} (gleiche Rechnungsnummer und Datum schon in finanz-O-mat)")
            if not trocken:
                db.sevdesk_import_speichern(k.objekt, k.sevdesk_id, "doppelt")
            continue
        erg.pruefen.extend(f"{k.titel}: {h}" for h in dict.fromkeys(h for b in k.belege for h in b.hinweise))
        if k.datei is None:
            erg.ohne_datei.append(k.titel)
        if trocken:
            erg.neu += 1
            erg.belege.extend(k.belege)
            continue
        if ablage is None or k.datei is None:
            _anlegen(db, cfg, k, "", "", erg)
            continue
        task = eintrag["paperless_task"] if eintrag and eintrag["status"] == "wartet" else ""
        if not task:
            try:
                task = ablage.hochladen(k)
            except PaperlessFehler as e:
                erg.fehler.append(f"{k.titel}: {e}")
                continue
            db.sevdesk_import_speichern(k.objekt, k.sevdesk_id, "wartet", paperless_task=task)
            erg.hochgeladen += 1
        offen.append((k, task))

    # Paperless verarbeitet die Dateien im Hintergrund (OCR); die Belege entstehen, sobald die Dokument-ID da ist.
    ende = uhr() + warten
    while offen:
        noch: list[tuple[Kandidat, str]] = []
        for k, task in offen:
            assert ablage is not None
            try:
                status, wert = ablage.aufgabe(task)
            except PaperlessFehler:
                noch.append((k, task))
                continue
            if status == "wartet":
                noch.append((k, task))
            elif status == "fehler":
                erg.fehler.append(f"{k.titel}: Paperless: {wert}")
                db.sevdesk_import_speichern(k.objekt, k.sevdesk_id, "fehler", task)
            elif status == "doppelt" and db.beleg_vorhanden("paperless", wert):
                erg.doppelt.append(f"{k.titel} (schon als Paperless-Dokument {wert} in finanz-O-mat)")
                db.sevdesk_import_speichern(k.objekt, k.sevdesk_id, "doppelt", task, wert)
            else:  # neu in Paperless, oder dort schon vorhanden, aber noch nicht in finanz-O-mat
                _anlegen(db, cfg, k, wert, task, erg)
        offen = noch
        if offen:
            if uhr() >= ende:
                erg.wartet = len(offen)
                break
            schlafen(3)
    return erg


# --- Schritt 3: Abgleich ---------------------------------------------------------------------

RE_MONAT = [
    (re.compile(r"^(\d{4})-(\d{1,2})$"), lambda m: f"{int(m[1]):04d}-{int(m[2]):02d}"),
    (re.compile(r"^(\d{1,2})[./](\d{4})$"), lambda m: f"{int(m[2]):04d}-{int(m[1]):02d}"),
    (re.compile(r"^(\d{4})-?Q([1-4])$", re.I), lambda m: f"{m[1]}-Q{m[2]}"),
    (re.compile(r"^Q([1-4])[./ ]?(\d{4})$", re.I), lambda m: f"{m[2]}-Q{m[1]}"),
]


def quartal(monat: str) -> str:
    jahr, mon = monat.split("-")
    return f"{jahr}-Q{(int(mon) - 1) // 3 + 1}"


def erwartet_lesen(pfad: Path) -> dict[str, Decimal]:
    """Abgegebene Zahllast je Monat aus einer CSV: ``Zeitraum;Zahllast``.

    Zeitraum als 2024-01, 01/2024 oder 01.2024, Quartale als 2023-Q1 oder Q1/2023; Beträge deutsch oder
    englisch geschrieben.
    Zeilen, die nicht passen (z. B. die Überschrift), werden übersprungen.
    """
    werte: dict[str, Decimal] = {}
    for zeile in pfad.read_text(encoding="utf-8-sig").splitlines():
        trenner = ";" if ";" in zeile else "\t" if "\t" in zeile else ","
        teile = [t.strip().strip('"') for t in zeile.split(trenner, 1 if trenner == "," else -1)]
        if len(teile) < 2:
            continue
        for muster, teil in RE_MONAT:
            if m := muster.match(teile[0]):
                betrag = betrag_parsen(teile[1].replace("€", ""))
                if betrag is not None:
                    werte[teil(m)] = betrag
                break
    return werte


def monatsvergleich(
    belege: list[Beleg], umrechner: Umrechner, versteuerung: str, monate: list[str],
    erwartet: dict[str, Decimal] | None = None, toleranz: Decimal = Decimal("1.00"),
) -> list[dict]:
    """Zahllast (Kz 83) je Monat, auf Wunsch neben den abgegebenen Werten.

    Liegt für ein Quartal (``2023-Q1``) statt der Monate ein abgegebener Wert vor, werden dessen Monate zu einer
    Zeile zusammengefasst.
    """
    erwartet = erwartet or {}
    gruppen: dict[str, list[str]] = {}
    for monat in monate:
        q = quartal(monat)
        gruppen.setdefault(q if q in erwartet and monat not in erwartet else monat, []).append(monat)
    zeilen = []
    for zeitraum, liste in gruppen.items():
        if zeitraum.endswith(tuple(f"Q{i}" for i in range(1, 5))):  # alle drei Monate, auch ohne Belege
            jahr, q = zeitraum.split("-Q")
            liste = [f"{jahr}-{m:02d}" for m in range(3 * int(q) - 2, 3 * int(q) + 1)]
        berichte = [berechnen(belege, int(m[:4]), int(m[5:]), umrechner, versteuerung) for m in liste]
        zahllast = sum((b.zahllast for b in berichte), Decimal(0))
        soll = erwartet.get(zeitraum)
        differenz = None if soll is None else zahllast - soll
        zeilen.append({
            "zeitraum": zeitraum,
            "zahllast": zahllast,
            "umsatzsteuer": sum((b.umsatzsteuer for b in berichte), Decimal(0)),
            "vorsteuer": sum((b.vorsteuer for b in berichte), Decimal(0)),
            "erwartet": soll,
            "differenz": differenz,
            "passt": differenz is None or abs(differenz) <= toleranz,
            "vollstaendig": all(b.vollstaendig for b in berichte),
        })
    return zeilen


def monate_von(erwartet: dict[str, Decimal]) -> list[str]:
    """Alle Monate der abgegebenen Zeiträume, Quartale aufgelöst."""
    ergebnis = set()
    for z in erwartet:
        if "-Q" in z:
            jahr, q = z.split("-Q")
            ergebnis.update(f"{jahr}-{m:02d}" for m in range(3 * int(q) - 2, 3 * int(q) + 1))
        else:
            ergebnis.add(z)
    return sorted(ergebnis)


def monate(belege: list[Beleg], versteuerung: str, von: str | None = None, bis: str | None = None) -> list[str]:
    from .berechnung import zeitraum

    alle = {z for b in belege if (z := zeitraum(b, versteuerung))}
    return sorted(m for m in alle if (not von or m >= von) and (not bis or m <= bis))
