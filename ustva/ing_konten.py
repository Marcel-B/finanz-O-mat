"""Umsätze und Saldo der ING-Konten über FinTS, ohne das Limit von PSD2 (4 Abrufe je Konto in 24 Stunden).

Wofür: Kommt eine Push-Nachricht der ING, soll die Buchung beim Öffnen des Reiters „Konten“ schon dastehen. Über
Enable Banking geht das nur viermal am Tag; FinTS ist die eigene Schnittstelle der Bank, über die der Inhaber
selbst abruft, da gilt das Limit nicht. Den Zugang (Produkt-ID, Zugangsnummer, PIN, Freigabe in der App) teilt
sich der Abruf mit dem Depot (ustva/depot.py).

Abgerufen wird beim Öffnen des Reiters (höchstens alle zwei Minuten), mit „Jetzt abrufen“ und im Hintergrund
stündlich von 7 bis 22 Uhr. Nur Konten, die schon über Enable Banking verbunden sind (gleiche IBAN), und nur in
die Kontenübersicht. In die Buchhaltung bringt sie weiter Enable Banking: Dessen Abruf übernimmt eine Zeile, die
schon über FinTS kam (gleicher Betrag, Buchungstag bis drei Tage auseinander), statt sie doppelt anzulegen; und
was Enable Banking schon geliefert hat, legt FinTS nicht noch einmal an.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from . import depot
from .config import Config
from .db import Datenbank
from .kontoabruf import CSV_TAGE, FINTS_PRAEFIX, SEPA_PRAEFIX
from .models import BankKonto, Umsatz

log = logging.getLogger(__name__)

ZEITRAUM = timedelta(days=14)  # die älteren Umsätze kommen über Enable Banking
BEIM_OEFFNEN = timedelta(minutes=2)  # beim Öffnen des Reiters nicht öfter abrufen
TAKT = timedelta(hours=1)  # im Hintergrund
TAGESZEIT = (7, 22)  # im Hintergrund nur von 7 bis 22 Uhr (deutsche Zeit)
# Länge der IBAN je Land, um sie vom Namen zu trennen, an dem sie ohne Leerzeichen hängt
IBAN_LAENGE = {"DE": 22, "AT": 20, "BE": 16, "CH": 21, "ES": 24, "FR": 27, "GB": 22, "IE": 22, "IT": 27, "LU": 20,
               "NL": 18}


@dataclass
class KontenAbruf:
    konten: list[str] = field(default_factory=list)  # Schlüssel der abgerufenen Konten
    neu: int = 0
    vorgemerkt: int = 0
    fehler: str = ""
    freigabe: bool = False
    uebersprungen: str = ""

    @property
    def ergebnis(self) -> str:
        if self.uebersprungen:
            return "uebersprungen"
        if self.freigabe:
            return "freigabe"
        return "fehler" if self.fehler else "ok"


def _text(wert) -> str:
    return re.sub(r"\s+", " ", str(wert or "")).strip()


def _name_und_iban(text: str) -> tuple[str, str]:
    """Mit den Standardregeln von mt940 steht die IBAN der Gegenseite (?31) ohne Trenner vor dem Namen (?32)."""
    laenge = IBAN_LAENGE.get(text[:2])
    if laenge and len(text) >= laenge and re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", text[:laenge]):
        return text[laenge:].strip(), text[:laenge]
    return text, ""


def umsatz_aus(t, k: BankKonto, heute: date | None = None) -> Umsatz | None:
    """Ein Umsatz aus einer Buchung von python-fints (``mt940.models.Transaction``)."""
    d = getattr(t, "data", t)
    menge = d.get("amount")
    betrag = getattr(menge, "amount", None)
    tag = d.get("entry_date") or d.get("date") or heute
    if betrag is None or tag is None:
        return None
    name, iban = _name_und_iban(_text(d.get("applicant_name") or d.get("recipient_name")))
    iban = _text(d.get("applicant_iban") or d.get("gvc_applicant_iban")).replace(" ", "").upper() or iban
    zweck = _text(SEPA_PRAEFIX.sub("", _text(d.get("purpose"))))
    typ = _text(d.get("posting_text"))
    waehrung = (getattr(menge, "currency", None) or k.waehrung or "EUR").upper()
    original, original_waehrung = None, ""
    if waehrung != (k.waehrung or "EUR").upper():
        original, original_waehrung = Decimal(betrag), waehrung
    return Umsatz(konto=k.konto, buchungsdatum=date(tag.year, tag.month, tag.day), betrag=Decimal(betrag),
                  partner=(name or zweck[:80] or typ).strip(), iban=iban, verwendungszweck=zweck, typ=typ[:80],
                  original_betrag=original, original_waehrung=original_waehrung)


def pruefsummen(schluessel: str, umsaetze: list[Umsatz]) -> None:
    """Aus dem Inhalt mit laufender Nummer je gleichem Umsatz (MT940 hat keine verlässliche Kennung).

    Jeder Abruf liefert dieselben Buchungen in derselben Reihenfolge, also dieselben Prüfsummen.
    """
    gesehen: Counter[str] = Counter()
    for u in sorted(umsaetze, key=lambda u: u.buchungsdatum):
        inhalt = "|".join(str(x) for x in (schluessel, "fints", u.buchungsdatum, u.betrag, u.partner,
                                           u.verwendungszweck))
        gesehen[inhalt] += 1
        u.pruefsumme = FINTS_PRAEFIX + hashlib.sha256(f"{inhalt}|{gesehen[inhalt]}".encode()).hexdigest()


def _schon_da(db: Datenbank, schluessel: str, u: Umsatz, vergeben: set[str]) -> bool:
    """Steht derselbe Umsatz schon aus einer anderen Quelle (Enable Banking, CAMT) in der Übersicht?"""
    tage = timedelta(days=CSV_TAGE)
    kandidaten = [z for z in db.konto_buchungen_von_bis(schluessel, u.buchungsdatum - tage, u.buchungsdatum + tage)
                  if not z["pruefsumme"].startswith(FINTS_PRAEFIX) and z["pruefsumme"] not in vergeben
                  and Decimal(z["betrag"]) == u.betrag]
    if not kandidaten:
        return False
    naechster = min(kandidaten, key=lambda z: abs((date.fromisoformat(z["buchungsdatum"]) - u.buchungsdatum).days))
    vergeben.add(naechster["pruefsumme"])
    return True


def _saldo(saldo, k: BankKonto, jetzt: datetime) -> tuple[str, str, str] | None:
    """Saldo, Währung und Stand aus ``mt940.models.Balance``."""
    menge = getattr(saldo, "amount", None)
    betrag = getattr(menge, "amount", None)
    if betrag is None:
        return None
    tag = getattr(saldo, "date", None)
    tag = date(tag.year, tag.month, tag.day) if tag else None
    stand = tag.isoformat() if tag and tag != jetzt.date() else jetzt.isoformat(timespec="seconds")
    return str(Decimal(betrag)), (getattr(menge, "currency", None) or k.waehrung or "EUR").upper(), stand


def ing(k: BankKonto) -> bool:
    """Ein Konto der ING (die Bankleitzahl steckt in der deutschen IBAN)."""
    return k.iban.startswith("DE") and k.iban[4:12] == depot.ING_BLZ


def faellig(db: Datenbank, jetzt: datetime, abstand: timedelta) -> bool:
    """Ist der letzte Abruf der Konten (auch ein fehlgeschlagener) mindestens ``abstand`` her?"""
    zuletzt = (db.fints_zustand(depot.ZUGANG) or {}).get("konten")
    if not zuletzt:
        return True
    return jetzt - datetime.fromisoformat(zuletzt) >= abstand


def tageszeit(jetzt: datetime, tz) -> bool:
    """Im Hintergrund nur tagsüber (TAGESZEIT, deutsche Zeit)."""
    von, bis = TAGESZEIT
    return von <= jetzt.astimezone(tz).hour < bis


def abrufen(db: Datenbank, cfg: Config, zugang: depot.IngFints | None = None, jetzt: datetime | None = None,
            warten: timedelta = depot.FREIGABE_WARTEN, hinweis: Callable[[str], None] | None = None,
            hintergrund: bool = False, wenn_aelter: timedelta | None = None) -> KontenAbruf:
    """Saldo, Buchungen und vorgemerkte Buchungen der ING-Konten, die schon verbunden sind.

    ``wenn_aelter``: nur, wenn der letzte Abruf mindestens so lange her ist (Öffnen des Reiters, Hintergrund).
    ``hintergrund``: ohne den Knopf (Öffnen des Reiters, Zeitplan); wartet die ING auf eine Freigabe in der App,
    wird nicht erneut gefragt, sonst käme bei jedem Öffnen eine neue Anfrage aufs Telefon.
    """
    jetzt = jetzt or datetime.now(UTC)
    with depot.SPERRE:
        a = KontenAbruf()
        stand = db.fints_zustand(depot.ZUGANG) or {}
        if wenn_aelter is not None and not faellig(db, jetzt, wenn_aelter):
            a.uebersprungen = "gerade erst abgerufen"
            return a
        if hintergrund and stand.get("freigabe"):
            a.uebersprungen = "wartet auf Freigabe in der ING-App; einmal von Hand abrufen"
            return a
        konten = {k.iban: k for k in db.bank_konten() if ing(k)}
        if not konten:
            a.uebersprungen = "kein Konto der ING verbunden"
            return a
        zeit = jetzt.isoformat(timespec="seconds")
        try:
            zugang = zugang or depot.IngFints(cfg, stand.get("daten"))
            sitzung = zugang.abrufen(warten=warten, hinweis=hinweis, depots=False,
                                     konten_ab=jetzt.date() - ZEITRAUM, ibans=set(konten))
        except depot.FreigabeNoetig as e:
            a.freigabe, a.fehler = True, str(e)
        except depot.DepotFehler as e:
            a.fehler = str(e)
        except Exception as e:  # noqa: BLE001 – Fehler der Bank oder von python-fints
            log.warning("ING-Konten über FinTS fehlgeschlagen", exc_info=True)
            a.fehler = f"{type(e).__name__}: {e}"
        db.fints_zustand_speichern(depot.ZUGANG, daten=zugang.zustand if zugang else None, fehler=a.fehler,
                                   freigabe=a.freigabe, versucht=zeit, konten=zeit)
        if a.fehler:
            return a
        for fk in sitzung.konten:
            k = konten[fk.iban]
            a.konten.append(k.schluessel)
            gebucht = [u for u in (umsatz_aus(t, k) for t in fk.gebucht) if u]
            pruefsummen(k.schluessel, gebucht)
            vergeben: set[str] = set()
            neu = [u for u in gebucht
                   if not db.konto_buchung_vorhanden(u.pruefsumme) and not _schon_da(db, k.schluessel, u, vergeben)]
            a.neu += db.konto_buchungen_speichern(k.schluessel, neu)
            vorgemerkt = [u for u in (umsatz_aus(t, k, jetzt.date()) for t in fk.vorgemerkt) if u]
            db.konto_vorgemerkt_ersetzen(k.schluessel, vorgemerkt)
            a.vorgemerkt += len(vorgemerkt)
            saldo = _saldo(fk.saldo, k, jetzt) if fk.saldo is not None else None
            db.bank_konto_fints(k.schluessel, zeit, *(saldo or ()))
        return a
