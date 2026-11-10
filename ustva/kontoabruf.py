"""Kontoumsätze automatisch über Enable Banking abrufen (PSD2, nur lesend).

Enable Banking (https://enablebanking.com) ist ein zugelassener Kontoinformationsdienst und reicht die
PSD2-Schnittstellen der Banken weiter, darunter N26, ING und bunq. Für die eigenen Konten ist der
„restricted mode“ kostenlos: Im Control Panel eine Anwendung für „Production“ anlegen, dabei den privaten
Schlüssel herunterladen, und die eigenen Konten mit „Activate by linking accounts“ verknüpfen. Die App
meldet sich mit einem selbst signierten JWT an (RS256, ``kid`` = Application ID).

Ablauf:

1. ``autorisierung_starten``: Enable Banking liefert eine Adresse; dort meldet man sich bei der Bank an und
   gibt den Abruf frei. Danach leitet die Bank auf die bei der Anwendung hinterlegte Rückleitungsadresse um,
   mit ``?code=…`` in der Adresse.
2. ``verbinden``: Aus dem Code wird eine Sitzung mit den freigegebenen Konten. Die Freigabe gilt höchstens so
   lange, wie die Bank erlaubt (meist 90 oder 180 Tage), danach erneut verbinden.
3. ``abrufen``: holt die gebuchten Umsätze der aktiven Konten und legt sie wie beim CSV-Import an. Vorgemerkte
   Umsätze bleiben draußen, bis die Bank sie bucht.

Umsätze, die schon per CSV eingelesen wurden, erkennt der Abruf am Konto, Betrag und Buchungstag (± ein paar
Tage) und legt sie nicht doppelt an. Umgekehrt überspringt der CSV-Import Zeilen, die schon abgerufen wurden.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt

from .config import Config
from .db import Datenbank
from .models import BankKonto, Umsatz

API = "https://api.enablebanking.com"
PRAEFIX = "eb:"  # Prüfsummen abgerufener Umsätze; CSV-Umsätze haben eine reine SHA-256
HOECHSTE_FREIGABE = timedelta(days=180)
ERSTER_ABRUF = timedelta(days=90)  # so weit liefern die meisten Banken ohne neue Freigabe zurück
UEBERLAPPUNG = timedelta(days=7)  # späte Buchungen mit älterem Buchungstag mitnehmen
CSV_TAGE = 3  # Abstand Buchungstag CSV ↔ Schnittstelle, in dem ein Umsatz als derselbe gilt
SEITEN = 100  # höchstens so viele Seiten Umsätze je Abruf
# Kurznamen der Banken, unter denen die Umsätze erscheinen. N26 wie beim CSV-Import, damit beides zusammenpasst.
KONTONAMEN = {"n26": "N26", "ing": "ING", "ing-diba": "ING", "bunq": "bunq"}


class KontoabrufFehler(RuntimeError):
    pass


def _schluessel_laden(pfad: Path) -> bytes:
    try:
        return pfad.read_bytes()
    except OSError as e:
        raise KontoabrufFehler(f"Privater Schlüssel {pfad} nicht lesbar: {e.strerror or e}") from e


class EnableBanking:
    """Zugriff auf die API von Enable Banking."""

    def __init__(self, app_id: str, schluessel: bytes, client: httpx.Client | None = None, api: str = API,
                 uhr=time.time):
        self.app_id = app_id
        self.schluessel = schluessel
        self.api = api.rstrip("/")
        self.client = client or httpx.Client(timeout=60)
        self._uhr = uhr
        self._token = ("", 0.0)

    @classmethod
    def aus_config(cls, cfg: Config, client: httpx.Client | None = None) -> EnableBanking:
        if not cfg.enable_banking_app_id or not cfg.enable_banking_schluessel:
            raise KontoabrufFehler(
                "Enable Banking ist nicht eingerichtet: ENABLE_BANKING_APP_ID und ENABLE_BANKING_SCHLUESSEL "
                "(Pfad zur .pem-Datei der Anwendung) setzen.")
        return cls(cfg.enable_banking_app_id, _schluessel_laden(cfg.enable_banking_schluessel), client)

    def _jwt(self) -> str:
        token, ablauf = self._token
        jetzt = int(self._uhr())
        if token and jetzt < ablauf - 300:
            return token
        try:
            token = jwt.encode({"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": jetzt,
                                "exp": jetzt + 3600}, self.schluessel, algorithm="RS256",
                               headers={"kid": self.app_id})
        except (jwt.PyJWTError, ValueError, TypeError) as e:  # kein gültiger RSA-Schlüssel
            raise KontoabrufFehler(f"Privater Schlüssel unbrauchbar: {e}") from e
        self._token = (token, jetzt + 3600)
        return token

    def _anfrage(self, methode: str, pfad: str, **kwargs) -> dict:
        try:
            antwort = self.client.request(methode, self.api + pfad, headers={"Authorization": f"Bearer {self._jwt()}"},
                                          **kwargs)
        except httpx.HTTPError as e:
            raise KontoabrufFehler(f"Enable Banking nicht erreichbar: {e}") from e
        if antwort.status_code >= 400:
            raise KontoabrufFehler(f"Enable Banking {antwort.status_code}: {_fehlertext(antwort)}")
        return antwort.json() if antwort.content else {}

    def anwendung(self) -> dict:
        return self._anfrage("GET", "/application")

    def banken(self, land: str = "DE") -> list[dict]:
        return self._anfrage("GET", "/aspsps", params={"country": land.upper()}).get("aspsps", [])

    def autorisierung_starten(self, bank: str, land: str, rueckleitung: str, gueltig_bis: datetime,
                              psu_type: str = "personal") -> tuple[str, str]:
        """Gibt die Adresse für die Freigabe bei der Bank und den ``state`` zurück."""
        state = secrets.token_urlsafe(16)
        antwort = self._anfrage("POST", "/auth", json={
            "access": {"valid_until": gueltig_bis.isoformat(), "balances": True, "transactions": True},
            "aspsp": {"name": bank, "country": land.upper()},
            "state": state,
            "redirect_url": rueckleitung,
            "psu_type": psu_type,
        })
        return antwort["url"], state

    def sitzung_anlegen(self, code: str) -> dict:
        return self._anfrage("POST", "/sessions", json={"code": code})

    def umsaetze(self, uid: str, von: date, bis: date | None = None) -> list[dict]:
        params: dict[str, str] = {"date_from": von.isoformat()}
        if bis:
            params["date_to"] = bis.isoformat()
        alle: list[dict] = []
        schluessel = None
        for _ in range(SEITEN):
            antwort = self._anfrage("GET", f"/accounts/{uid}/transactions",
                                    params=params | ({"continuation_key": schluessel} if schluessel else {}))
            alle += antwort.get("transactions") or []
            neu = antwort.get("continuation_key")
            if not neu or neu == schluessel:
                break
            schluessel = neu
        return alle


def _fehlertext(antwort: httpx.Response) -> str:
    try:
        daten = antwort.json()
    except ValueError:
        return antwort.text[:300] or antwort.reason_phrase
    if isinstance(daten, dict):
        teile = [str(daten[k]) for k in ("message", "detail", "code", "error") if daten.get(k)]
        if teile:
            return " – ".join(dict.fromkeys(teile))[:500]
    return json.dumps(daten, ensure_ascii=False)[:300]


# Freigabe

def freigabe_bis(bank: dict, jetzt: datetime) -> datetime:
    """So lange wie die Bank erlaubt (``maximum_consent_validity`` in Sekunden), höchstens 180 Tage."""
    hoechstens = bank.get("maximum_consent_validity")
    dauer = HOECHSTE_FREIGABE
    if isinstance(hoechstens, int | float) and hoechstens > 0:
        dauer = min(dauer, timedelta(seconds=hoechstens))
    # Etwas Luft, damit die Uhr der Bank die Freigabe nicht als zu lang ablehnt
    return jetzt + dauer - timedelta(minutes=5)


def bank_finden(banken: list[dict], name: str) -> dict:
    gesucht = name.strip().lower()
    exakt = [b for b in banken if b.get("name", "").lower() == gesucht]
    if exakt:
        return exakt[0]
    teil = [b for b in banken if gesucht in b.get("name", "").lower()]
    if len(teil) == 1:
        return teil[0]
    if not teil:
        raise KontoabrufFehler(f"Keine Bank „{name}“ gefunden. Verfügbare Namen zeigt „bank banken“.")
    namen = ", ".join(sorted(b["name"] for b in teil))
    raise KontoabrufFehler(f"„{name}“ passt auf mehrere Banken: {namen}. Bitte genauer angeben.")


def code_aus(eingabe: str, state: str | None = None) -> str:
    """Code aus der Rückleitungsadresse (oder der Code selbst)."""
    eingabe = eingabe.strip()
    if "?" not in eingabe and "=" not in eingabe:
        if not eingabe:
            raise KontoabrufFehler("Keine Adresse eingegeben.")
        return eingabe
    abfrage = parse_qs(urlsplit(eingabe).query if "?" in eingabe else eingabe)
    if "error" in abfrage:
        grund = (abfrage.get("error_description") or abfrage["error"])[0]
        raise KontoabrufFehler(f"Die Bank hat die Freigabe nicht erteilt: {grund}")
    if state is not None and abfrage.get("state", [state])[0] != state:
        raise KontoabrufFehler("Die Adresse gehört nicht zu dieser Freigabe (state passt nicht). Bitte neu starten.")
    if not abfrage.get("code"):
        raise KontoabrufFehler("In der Adresse steht kein code=…. Bitte die ganze Adresse aus der Adresszeile "
                               "kopieren.")
    return abfrage["code"][0]


def kontoname(bank: str) -> str:
    return KONTONAMEN.get(bank.strip().lower(), bank.strip())


def sitzung_uebernehmen(db: Datenbank, sitzung: dict, bank: str, land: str) -> tuple[list[BankKonto], list[BankKonto]]:
    """Speichert die Konten einer neuen Sitzung. Gibt (neue, erneuerte) Konten zurück.

    Ein schon bekanntes Konto (gleiche IBAN) behält Namen und Aktiv-Schalter, bekommt aber die neue uid.
    Neue Konten sind zunächst aus, damit private Konten nicht ungefragt in der Umsatzsteuer landen.
    """
    gueltig = (sitzung.get("access") or {}).get("valid_until", "")
    neu: list[BankKonto] = []
    erneuert: list[BankKonto] = []
    for a in sitzung.get("accounts") or []:
        if isinstance(a, str):  # ältere Antworten: nur uids, Details in accounts_data
            a = {"uid": a}
        iban = ((a.get("account_id") or {}).get("iban") or "").replace(" ", "").upper()
        schluessel = iban or a.get("identification_hash") or a["uid"]
        vorher = db.bank_konto(schluessel)
        k = BankKonto(
            schluessel=schluessel, uid=a["uid"], sitzung=sitzung["session_id"], bank=bank, land=land.upper(),
            konto=vorher.konto if vorher else kontoname(bank), iban=iban,
            bezeichnung=a.get("name") or a.get("product") or (vorher.bezeichnung if vorher else ""),
            waehrung=(a.get("currency") or "EUR").upper(), aktiv=vorher.aktiv if vorher else False,
            gueltig_bis=gueltig, abgerufen=vorher.abgerufen if vorher else "", bis=vorher.bis if vorher else "",
        )
        db.bank_konto_speichern(k)
        (erneuert if vorher else neu).append(k)
    return neu, erneuert


def tage_gueltig(k: BankKonto, jetzt: datetime) -> int | None:
    if not k.gueltig_bis:
        return None
    try:
        bis = datetime.fromisoformat(k.gueltig_bis.replace("Z", "+00:00"))
    except ValueError:
        return None
    if bis.tzinfo is None:
        bis = bis.replace(tzinfo=UTC)
    return (bis - jetzt).days


# Umsätze

SEPA_PRAEFIX = re.compile(r"^(EREF|KREF|MREF|CRED|DEBT|SVWZ|ABWA|ABWE|IBAN|BIC)\+", re.IGNORECASE)


def _betrag(wert) -> Decimal | None:
    try:
        return Decimal(str(wert).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _datum(tx: dict) -> date | None:
    for feld in ("booking_date", "value_date", "transaction_date"):
        if tx.get(feld):
            try:
                return date.fromisoformat(str(tx[feld])[:10])
            except ValueError:
                continue
    return None


def umsatz_aus(tx: dict, konto: BankKonto) -> Umsatz | None:
    """Umsatz aus einer Buchung der Schnittstelle; None für vorgemerkte und unlesbare Buchungen."""
    if tx.get("status") not in (None, "", "BOOK"):
        return None
    menge = tx.get("transaction_amount") or {}
    betrag = _betrag(menge.get("amount"))
    buchungsdatum = _datum(tx)
    if betrag is None or buchungsdatum is None:
        return None
    richtung = tx.get("credit_debit_indicator")
    if richtung == "DBIT":
        betrag = -abs(betrag)
    elif richtung == "CRDT":
        betrag = abs(betrag)
    # Gegenüber: bei Eingängen der Zahler, bei Ausgängen der Empfänger
    seite = ("debtor", "creditor") if richtung == "CRDT" else ("creditor", "debtor")
    partner = next(((tx.get(s) or {}).get("name") or "" for s in seite if (tx.get(s) or {}).get("name")), "")
    iban = next(((tx.get(f"{s}_account") or {}).get("iban") or "" for s in seite
                 if (tx.get(f"{s}_account") or {}).get("iban")), "")
    zweck = " ".join(t for t in (SEPA_PRAEFIX.sub("", str(z)).strip() for z in tx.get("remittance_information") or [])
                     if t)
    if not partner:
        partner = zweck[:80]
    code = tx.get("bank_transaction_code") or {}
    typ = (code.get("description") or code.get("code") or "") if isinstance(code, dict) else str(code)
    original, waehrung = None, ""
    kurs = tx.get("exchange_rate") or {}
    instruiert = kurs.get("instructed_amount") or {}
    if instruiert.get("currency") and instruiert["currency"].upper() != (menge.get("currency") or "EUR").upper():
        original, waehrung = _betrag(instruiert.get("amount")), instruiert["currency"].upper()
        if original is not None:
            original = -abs(original) if betrag < 0 else abs(original)
    return Umsatz(konto=konto.konto, buchungsdatum=buchungsdatum, betrag=betrag, partner=partner.strip(),
                  iban=iban.replace(" ", "").upper(), verwendungszweck=zweck, typ=typ, original_betrag=original,
                  original_waehrung=waehrung)


def pruefsummen(buchungen: list[tuple[dict, Umsatz]], konto: BankKonto) -> None:
    """Stabile Prüfsumme je Buchung: aus der Kennung der Bank, sonst aus dem Inhalt.

    Die uid des Kontos wechselt mit jeder Freigabe, deshalb zählt der Schlüssel (IBAN). Gleiche Buchungen ohne
    Kennung am selben Tag (zwei gleiche Kartenzahlungen) bleiben über eine laufende Nummer getrennt.
    """
    gesehen: Counter[str] = Counter()
    for tx, u in buchungen:
        kennung = tx.get("entry_reference") or tx.get("transaction_id")
        if kennung:
            inhalt = f"{konto.schluessel}|id|{kennung}"
        else:
            inhalt = "|".join(str(x) for x in (konto.schluessel, u.buchungsdatum, u.betrag, u.partner,
                                               u.verwendungszweck))
            gesehen[inhalt] += 1
            inhalt += f"|{gesehen[inhalt]}"
        u.pruefsumme = PRAEFIX + hashlib.sha256(inhalt.encode()).hexdigest()


def aus_csv(u: Umsatz) -> bool:
    return not u.pruefsumme.startswith(PRAEFIX)


def _gleich(a: Umsatz, b: Umsatz) -> bool:
    return a.konto == b.konto and a.betrag == b.betrag


def schon_per_csv(db: Datenbank, u: Umsatz, vergeben: set[int]) -> Umsatz | None:
    """Der per CSV eingelesene Umsatz, der diesem abgerufenen entspricht (zeitlich nächster)."""
    tage = timedelta(days=CSV_TAGE)
    kandidaten = [v for v in db.umsaetze_von_bis(u.konto, u.buchungsdatum - tage, u.buchungsdatum + tage)
                  if aus_csv(v) and v.id not in vergeben and _gleich(u, v)]
    return min(kandidaten, key=lambda v: abs((v.buchungsdatum - u.buchungsdatum).days), default=None)


def schon_abgerufen(db: Datenbank, umsaetze: list[Umsatz]) -> list[bool]:
    """Für den CSV-Import: welche Zeilen schon über die Schnittstelle kamen.

    Gleiche Umsätze (Konto, Tag, Betrag) zählen einzeln: Gibt es zwei abgerufene, gelten die ersten zwei
    gleichen CSV-Zeilen als vorhanden, eine dritte nicht.
    """
    zaehler: Counter[tuple] = Counter()
    ergebnis = []
    for u in umsaetze:
        schluessel = (u.konto, u.buchungsdatum, u.betrag)
        zaehler[schluessel] += 1
        abgerufen = [v for v in db.umsaetze_von_bis(u.konto, u.buchungsdatum, u.buchungsdatum)
                     if not aus_csv(v) and v.betrag == u.betrag]
        ergebnis.append(not db.umsatz_vorhanden(u.pruefsumme) and len(abgerufen) >= zaehler[schluessel])
    return ergebnis


@dataclass
class Abruf:
    konto: BankKonto
    neu: int = 0
    vorhanden: int = 0
    aus_csv: int = 0
    vorgemerkt: int = 0
    fehler: str = ""
    umsaetze: list[Umsatz] = field(default_factory=list)  # neue Umsätze (beim Probelauf nur gezeigt)


@dataclass
class Abrufe:
    konten: list[Abruf] = field(default_factory=list)
    zugeordnet: int = 0

    @property
    def fehler(self) -> list[str]:
        return [f"{a.konto.konto} ({a.konto.iban or a.konto.schluessel}): {a.fehler}" for a in self.konten if a.fehler]


def abrufen(db: Datenbank, cfg: Config, eb: EnableBanking, von: date | None = None, heute: date | None = None,
            jetzt: datetime | None = None, umrechner=None, trocken: bool = False, nur: str = "") -> Abrufe:
    """Holt die Umsätze der eingeschalteten Konten (``nur``: nur dieses, auch wenn es aus ist).

    ``trocken``: Probelauf, der nichts speichert; die neuen Umsätze stehen in ``Abruf.umsaetze``.
    """
    from .bank import abgleich_ausfuehren, ignorier_grund

    heute = heute or date.today()
    jetzt = jetzt or datetime.now(UTC)
    ergebnis = Abrufe()
    regeln = db.bank_regeln()
    nur = nur.replace(" ", "").upper()
    for k in (k for k in db.bank_konten() if (nur in (k.schluessel.upper(), k.iban) if nur else k.aktiv)):
        a = Abruf(k)
        ergebnis.konten.append(a)
        tage = tage_gueltig(k, jetzt)
        if tage is not None and tage < 0:
            a.fehler = "Freigabe abgelaufen, bitte neu verbinden"
            continue
        start = von or (date.fromisoformat(k.bis) - UEBERLAPPUNG if k.bis else heute - ERSTER_ABRUF)
        try:
            roh = eb.umsaetze(k.uid, start)
        except KontoabrufFehler as e:
            a.fehler = str(e)
            continue
        buchungen = []
        for tx in roh:
            if (u := umsatz_aus(tx, k)) is None:
                a.vorgemerkt += 1
            else:
                buchungen.append((tx, u))
        pruefsummen(buchungen, k)
        vergeben: set[int] = set()
        for _, u in buchungen:
            if db.umsatz_vorhanden(u.pruefsumme):
                a.vorhanden += 1
                continue
            if (alt := schon_per_csv(db, u, vergeben)) is not None:
                # Die CSV-Zeile bleibt mit Zuordnung und Markierung, bekommt aber die Prüfsumme der Schnittstelle
                alt.pruefsumme = u.pruefsumme
                if not trocken:
                    db.umsatz_aktualisieren(alt)
                vergeben.add(alt.id)
                a.aus_csv += 1
                continue
            if grund := ignorier_grund(u, regeln, cfg.bank_ignorieren):
                u.ignoriert, u.ignoriert_grund = True, grund
            if not trocken:
                db.umsatz_anlegen(u)
            a.umsaetze.append(u)
            a.neu += 1
        if trocken:
            continue
        letzte = max((u.buchungsdatum for _, u in buchungen), default=None)
        if letzte and (not k.bis or letzte.isoformat() > k.bis):
            k.bis = letzte.isoformat()
        k.abgerufen = jetzt.isoformat(timespec="seconds")
        db.bank_konto_speichern(k)
    if not trocken and any(a.neu for a in ergebnis.konten):
        ergebnis.zugeordnet = abgleich_ausfuehren(db, umrechner)
    return ergebnis
