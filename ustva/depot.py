"""Zugang zur ING über FinTS (HBCI): Bestand des Direkt-Depots für den Reiter „Konten“.

Über denselben Zugang holt ustva/ing_konten.py Umsätze und Saldo der ING-Konten, ohne das Limit von PSD2.

Enable Banking (PSD2) liefert nur Zahlungskonten, keine Depots. Die ING bietet über FinTS für das Direkt-Depot den
Depotbestand mit Details je Wertpapier an, aber keine Depotumsätze (https://www.ing.de/hilfe/log-in/fints/).

Dafür braucht die App
- eine FinTS-Produkt-ID der Deutschen Kreditwirtschaft (``FINTS_PRODUKT_ID``, kostenlos, Registrierung unter
  https://www.fints.org/de/hersteller/produktregistrierung, bis zu zwei Wochen),
- Benutzername (``ING_FINTS_ZUGANG``) und Passwort (``ING_FINTS_PIN``) des Online-Bankings, wie beim Login im
  Browser (https://www.ing.de/hilfe/log-in/fints/); vor der Umstellung der ING 2026 Zugangsnummer und PIN.

Verlangt die ING eine starke Kundenauthentifizierung, schickt sie eine Freigabe in die ING-App. Der Abruf wartet
darauf bis zu zwei Minuten. Im Hintergrund wird danach nicht erneut gefragt, bis ein Abruf von Hand (Knopf oder
``bank depot``) die Freigabe wieder bekommen hat; sonst käme bei jedem Abruf eine neue Anfrage aufs Telefon.

Nur eine Übersicht, mit der Buchhaltung hat das Depot nichts zu tun.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .config import Config
from .db import Datenbank

log = logging.getLogger(__name__)

ING_BLZ = "50010517"
ING_BIC = "INGDDEFFXXX"  # python-fints braucht für die Depotaufstellung eine BIC (Länderkennung daraus)
ING_FINTS = "https://fints.ing.de/fints/"
ZUGANG = "ing"  # Schlüssel des gespeicherten FinTS-Zustands
FREIGABE_WARTEN = timedelta(minutes=2)
ERSTE_NACHFRAGE = 5  # Sekunden bis zur ersten Nachfrage, ob in der App freigegeben wurde
NACHFRAGE = 3
DEPOT_KONTOARTEN = range(50, 60)  # FinTS-Kontoart 50–59: Depot
SPERRE = threading.Lock()  # immer nur ein Dialog mit der ING (Hintergrund, Knopf, Kommandozeile)


class DepotFehler(RuntimeError):
    pass


class FreigabeNoetig(DepotFehler):
    """Die ING wollte eine Freigabe in der App, die nicht rechtzeitig kam (oder im Hintergrund nicht gefragt wird)."""


class AnmeldungAbgelehnt(DepotFehler):
    """Die ING hat die Anmeldung abgelehnt (Passwort, Benutzername, Produkt-ID oder gesperrter Zugang).

    Danach wird es automatisch nicht erneut versucht: Nach mehreren Fehlversuchen sperrt die ING den Zugang.
    """


def angehalten(stand: dict) -> str:
    """Warum ein automatischer Abruf (Hintergrund, Öffnen des Reiters) gerade nicht bei der ING anfragt."""
    if stand.get("abgelehnt"):
        return "die ING hat die Anmeldung abgelehnt; Zugangsdaten prüfen und einmal von Hand abrufen"
    if stand.get("freigabe"):
        return "wartet auf Freigabe in der ING-App; einmal von Hand abrufen"
    return ""


def _ablehnung(antworten: list[tuple[str, str]], fehler: Exception) -> AnmeldungAbgelehnt:
    """Die Antworten der ING mit Fehlercode; python-fints meldet bei jeder Ablehnung nur „PIN wrong?“."""
    texte = "; ".join(dict.fromkeys(f"{code} {text}".strip() for code, text in antworten if code.startswith("9")))
    return AnmeldungAbgelehnt(
        f"Die ING hat die Anmeldung abgelehnt ({texte or type(fehler).__name__}). Benutzername und Passwort des "
        "Online-Bankings und die Produkt-ID prüfen (finanz-o-mat-fints); eine neue Produkt-ID kann bei der ING erst "
        "nach Tagen freigeschaltet sein. Automatisch wird es nicht erneut versucht, nach mehreren Fehlversuchen "
        "sperrt die ING den Zugang.")


def eingerichtet(cfg: Config) -> bool:
    return bool(cfg.fints_produkt_id and cfg.ing_fints_zugang and cfg.ing_fints_pin)


@dataclass
class Position:
    isin: str
    name: str
    stueck: Decimal
    kurs: Decimal | None = None
    waehrung: str = "EUR"
    kursdatum: date | None = None
    wert: Decimal | None = None
    einstand: Decimal | None = None  # Einstandskurs je Stück

    @property
    def einstandswert(self) -> Decimal | None:
        return None if self.einstand is None else (self.stueck * self.einstand).quantize(Decimal("0.01"))


@dataclass
class Depot:
    nummer: str
    name: str = "Direkt-Depot"
    bank: str = "ING"
    waehrung: str = "EUR"
    positionen: list[Position] = field(default_factory=list)

    @property
    def schluessel(self) -> str:
        return f"fints:{ING_BLZ}:{self.nummer}"

    @property
    def wert(self) -> Decimal:
        return sum((p.wert or Decimal(0) for p in self.positionen), Decimal(0))


@dataclass
class FintsKonto:
    """Ein Zahlungskonto aus einer Sitzung: Saldo und Buchungen, wie python-fints sie liefert (mt940)."""

    iban: str
    name: str = ""  # Produktname laut Bank, z. B. „Extra-Konto“
    waehrung: str = "EUR"
    saldo: object | None = None  # mt940.models.Balance
    gebucht: list = field(default_factory=list)  # mt940.models.Transaction
    vorgemerkt: list = field(default_factory=list)


@dataclass
class Sitzung:
    depots: list[Depot] = field(default_factory=list)
    konten: list[FintsKonto] = field(default_factory=list)
    depot_ibans: set[str] = field(default_factory=set)  # bei ``konten`` übergangen: Depots haben keine Umsätze


@dataclass
class DepotAbruf:
    depots: list[Depot] = field(default_factory=list)
    fehler: str = ""
    freigabe: bool = False  # wartet auf eine Freigabe in der ING-App
    uebersprungen: str = ""

    @property
    def ergebnis(self) -> str:
        if self.uebersprungen:
            return "uebersprungen"
        if self.freigabe:
            return "freigabe"
        return "fehler" if self.fehler else "ok"


def _dec(wert) -> Decimal | None:
    """python-fints liefert die Werte aus MT535 als float; über str() bleiben die Stellen der Bank erhalten."""
    if wert is None:
        return None
    try:
        return Decimal(str(wert))
    except InvalidOperation:
        return None


def position_aus(h) -> Position | None:
    """Eine Position aus ``fints.models.Holding``."""
    isin = (getattr(h, "ISIN", None) or "").strip()
    if not isin:
        return None
    stueck = _dec(getattr(h, "pieces", None)) or Decimal(0)
    kurs = _dec(getattr(h, "market_value", None))
    wert = _dec(getattr(h, "total_value", None))
    if wert is None and kurs is not None:
        wert = (stueck * kurs).quantize(Decimal("0.01"))
    return Position(isin=isin, name=(getattr(h, "name", None) or isin).strip(), stueck=stueck, kurs=kurs,
                    waehrung=(getattr(h, "value_symbol", None) or "EUR").upper(),
                    kursdatum=getattr(h, "valuation_date", None),
                    wert=wert, einstand=_dec(getattr(h, "acquisitionprice", None)))


def _depot(a: dict) -> bool:
    """Kontoart 50–59, der Geschäftsvorfall Depotaufstellung (HKWPD) oder „Depot“ im Produktnamen."""
    if not a.get("account_number"):
        return False
    if a.get("type") in DEPOT_KONTOARTEN:
        return True
    if any(getattr(op, "name", op) == "GET_HOLDINGS" and erlaubt
           for op, erlaubt in (a.get("supported_operations") or {}).items()):
        return True
    return "depot" in (a.get("product_name") or "").lower()


def depots_aus(info: dict) -> list[dict]:
    """Die Depots aus den Kontoinformationen der Bank (UPD)."""
    return [a for a in info.get("accounts", []) if _depot(a)]


def konten_text(info: dict) -> str:
    """Was die Bank als Konten meldet, für eine Fehlermeldung: Kontoart, Produkt, letzte vier Ziffern."""
    teile = [f"Kontoart {a.get('type')} „{(a.get('product_name') or '').strip() or '?'}“ "
             f"…{str(a.get('account_number') or '')[-4:]}" for a in info.get("accounts", [])]
    return "; ".join(teile) or "keine"


def _client(cfg: Config, zustand: bytes | None):
    from fints.client import FinTS3PinTanClient

    return FinTS3PinTanClient(ING_BLZ, cfg.ing_fints_zugang, cfg.ing_fints_pin, ING_FINTS,
                              product_id=cfg.fints_produkt_id, from_data=zustand)


def _antworten_merken(f) -> list[tuple[str, str]]:
    """Merkt sich die Rückmeldungen der Bank (Code, Text).

    python-fints schreibt sie während der Anmeldung nicht ins Log und wirft bei jeder Ablehnung dieselbe Ausnahme.
    """
    antworten: list[tuple[str, str]] = []
    verarbeiten = getattr(f, "_process_response", None)
    if verarbeiten is None:
        return antworten

    def merken(dialog, segment, antwort):
        antworten.append((str(getattr(antwort, "code", "") or ""), str(getattr(antwort, "text", "") or "")))
        return verarbeiten(dialog, segment, antwort)

    f._process_response = merken
    return antworten


class IngFints:
    """Der Weg zur ING über python-fints; in den Tests ersetzt ``client_fabrik`` den echten Client."""

    def __init__(self, cfg: Config, zustand: bytes | None = None, client_fabrik: Callable | None = None,
                 schlafen: Callable[[float], None] = time.sleep, uhr: Callable[[], float] = time.monotonic):
        if not eingerichtet(cfg):
            raise DepotFehler("Depotabruf nicht eingerichtet: FINTS_PRODUKT_ID, ING_FINTS_ZUGANG und ING_FINTS_PIN "
                              "setzen (siehe README)")
        self.cfg = cfg
        self.zustand = zustand
        self._fabrik = client_fabrik or _client
        self._schlafen, self._uhr = schlafen, uhr

    def _freigeben(self, f, antwort, bis: float, hinweis: Callable[[str], None] | None):
        """Wartet auf die Freigabe in der App, solange die Bank eine verlangt (``NeedTANResponse``)."""
        from fints.client import NeedTANResponse

        gemeldet = False
        while isinstance(antwort, NeedTANResponse):
            if not antwort.decoupled:
                raise DepotFehler("Die ING verlangt eine TAN statt einer Freigabe in der App; das kann der Abruf "
                                  "nicht. Im Online-Banking die App-Freigabe als Verfahren wählen.")
            if bis <= self._uhr():
                raise FreigabeNoetig("Freigabe in der ING-App nicht rechtzeitig bestätigt")
            if not gemeldet:
                gemeldet = True
                text = (getattr(antwort, "challenge", "") or "").strip()
                log.info("Depotabruf: ING wartet auf Freigabe in der App%s", f" ({text})" if text else "")
                if hinweis:
                    hinweis("Bitte den Zugriff in der ING-App freigeben" + (f": {text}" if text else "."))
                self._schlafen(ERSTE_NACHFRAGE)
            else:
                self._schlafen(NACHFRAGE)
            antwort = f.send_tan(antwort, "")
        return antwort

    def _verfahren_waehlen(self, f) -> None:
        """Freigabe in der App bevorzugen; die ING bietet in der Regel nur ein Verfahren an."""
        if f.get_current_tan_mechanism():
            return
        f.fetch_tan_mechanisms()
        verfahren = [code for code in f.get_tan_mechanisms() if code != "999"]
        if verfahren:
            f.set_tan_mechanism(verfahren[0])

    def _depots(self, f, bis: float, hinweis) -> list[Depot]:
        from fints.models import SEPAAccount

        info = f.get_information()
        gefunden = depots_aus(info)
        if not gefunden:
            raise DepotFehler(f"Die ING meldet für diesen Zugang kein Depot (gemeldete Konten: {konten_text(info)})")
        try:  # die SEPA-Konten tragen die BIC, die Kontoinformationen nicht
            sepa = {a.iban: a for a in f.get_sepa_accounts() or [] if a.iban}
        except Exception:  # noqa: BLE001 – dann die BIC der ING
            log.debug("SEPA-Konten nicht abrufbar", exc_info=True)
            sepa = {}
        depots = []
        for d in gefunden:
            bekannt = sepa.get(d.get("iban"))
            konto = SEPAAccount(iban=d.get("iban"), bic=(bekannt.bic if bekannt else None) or ING_BIC,
                                accountnumber=d["account_number"], subaccount=d.get("subaccount_number"),
                                blz=ING_BLZ)
            bestand = self._freigeben(f, f.get_holdings(konto), bis, hinweis) or []
            depots.append(Depot(nummer=d["account_number"], name=(d.get("product_name") or "Depot").strip(),
                                waehrung=(d.get("currency") or "EUR").upper(),
                                positionen=[p for p in map(position_aus, bestand) if p]))
        return depots

    def _konten(self, f, ab: date, ibans: set[str] | None, bis: float, hinweis,
                depot_ibans: set[str] | None = None) -> list[FintsKonto]:
        """Saldo, gebuchte und vorgemerkte Buchungen je Konto (``ibans``: nur diese, ``None``: alle).

        Depots meldet die ING mit IBAN unter den SEPA-Konten; sie kommen nach ``depot_ibans`` statt in die Liste
        (Saldo und Umsätze lehnt die ING für sie mit 9210 ab).

        python-fints hängt die vorgemerkten Buchungen (MT942) hinten an die gebuchten an, ohne sie zu kennzeichnen;
        deshalb einmal ohne und einmal mit: was beim zweiten Mal hinter den gebuchten kommt, ist vorgemerkt.
        """
        ergebnis = []
        try:
            angaben = {(a.get("iban") or "").replace(" ", "").upper(): a
                       for a in f.get_information().get("accounts", [])}
        except Exception:  # noqa: BLE001 – nur für den Namen des Kontos
            angaben = {}
        for konto in self._freigeben(f, f.get_sepa_accounts(), bis, hinweis) or []:
            iban = (konto.iban or "").replace(" ", "").upper()
            if not iban or (ibans is not None and iban not in ibans):
                continue
            angabe = angaben.get(iban, {})
            if _depot(angabe):
                if depot_ibans is not None:
                    depot_ibans.add(iban)
                continue
            fk = FintsKonto(iban, name=(angabe.get("product_name") or "").strip(),
                            waehrung=(angabe.get("currency") or "EUR").upper(),
                            saldo=self._freigeben(f, f.get_balance(konto), bis, hinweis))
            fk.gebucht = list(self._freigeben(f, f.get_transactions(konto, ab), bis, hinweis) or [])
            alle = list(self._freigeben(f, f.get_transactions(konto, ab, include_pending=True), bis, hinweis) or [])
            fk.vorgemerkt = alle[len(fk.gebucht):]
            ergebnis.append(fk)
        return ergebnis

    def abrufen(self, warten: timedelta = FREIGABE_WARTEN, hinweis: Callable[[str], None] | None = None,
                depots: bool = True, konten_ab: date | None = None, ibans: set[str] | None = None) -> Sitzung:
        """Eine Sitzung: die Depots und, mit ``konten_ab``, Saldo und Buchungen der Konten (``ibans``: nur diese)."""
        from fints.exceptions import FinTSClientPINError, FinTSClientTemporaryAuthError

        bis = self._uhr() + warten.total_seconds()
        f = self._fabrik(self.cfg, self.zustand)
        antworten = _antworten_merken(f)
        try:
            self._verfahren_waehlen(f)
            with f:
                self._freigeben(f, f.init_tan_response, bis, hinweis)
                s = Sitzung()
                if depots:
                    s.depots = self._depots(f, bis, hinweis)
                if konten_ab:
                    s.konten = self._konten(f, konten_ab, ibans, bis, hinweis, s.depot_ibans)
                return s
        except (FinTSClientPINError, FinTSClientTemporaryAuthError) as e:
            raise _ablehnung(antworten, e) from e
        finally:
            try:  # Systemkennung, Bank- und Nutzerdaten fürs nächste Mal (ohne PIN)
                self.zustand = f.deconstruct(including_private=True)
            except Exception:  # noqa: BLE001 – ein halb geöffneter Client darf den Fehler nicht verdecken
                log.debug("FinTS-Zustand nicht gespeichert", exc_info=True)


def abrufen(db: Datenbank, cfg: Config, zugang: IngFints | None = None, jetzt: datetime | None = None,
            warten: timedelta = FREIGABE_WARTEN, hinweis: Callable[[str], None] | None = None,
            hintergrund: bool = False) -> DepotAbruf:
    """Holt den Bestand aller Depots des Zugangs und ersetzt die gespeicherten Positionen."""
    with SPERRE:
        return _abrufen(db, cfg, zugang, jetzt or datetime.now(UTC), warten, hinweis, hintergrund)


def _abrufen(db, cfg, zugang, jetzt, warten, hinweis, hintergrund) -> DepotAbruf:
    a = DepotAbruf()
    stand = db.fints_zustand(ZUGANG) or {}
    if hintergrund and angehalten(stand):
        a.uebersprungen = angehalten(stand)
        return a
    abgelehnt = None
    try:
        zugang = zugang or IngFints(cfg, stand.get("daten"))
        a.depots = zugang.abrufen(warten=warten, hinweis=hinweis).depots
        abgelehnt = False
    except FreigabeNoetig as e:
        a.freigabe, a.fehler = True, str(e)
        abgelehnt = False  # die Anmeldung selbst hat geklappt
    except AnmeldungAbgelehnt as e:
        a.fehler, abgelehnt = str(e), True
        log.warning("Depotabruf: %s", e)
    except DepotFehler as e:
        a.fehler = str(e)
    except Exception as e:  # noqa: BLE001 – Fehler der Bank oder von python-fints landen beim Depot
        log.warning("Depotabruf fehlgeschlagen", exc_info=True)
        a.fehler = f"{type(e).__name__}: {e}"
    zeit = jetzt.isoformat(timespec="seconds")
    db.fints_zustand_speichern(ZUGANG, daten=zugang.zustand if zugang else None, fehler=a.fehler,
                               freigabe=a.freigabe, versucht=zeit, abgelehnt=abgelehnt)
    for d in a.depots:
        db.depot_speichern(d, zeit)
    return a
