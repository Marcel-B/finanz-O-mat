"""Ablage der Belege und Kurse in SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Beleg, Kontakt, Umsatz
from .steuerfaelle import Art, Steuerfall

SCHEMA = """
CREATE TABLE IF NOT EXISTS belege (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    art TEXT NOT NULL,
    steuerfall TEXT NOT NULL,
    rechnungsdatum TEXT,
    zahlungsdatum TEXT,
    netto TEXT NOT NULL,
    steuersatz TEXT NOT NULL,
    steuer TEXT,
    waehrung TEXT NOT NULL DEFAULT 'EUR',
    kurs TEXT,
    partner TEXT NOT NULL DEFAULT '',
    partner_ustid TEXT NOT NULL DEFAULT '',
    rechnungsnummer TEXT NOT NULL DEFAULT '',
    beschreibung TEXT NOT NULL DEFAULT '',
    quelle TEXT NOT NULL DEFAULT 'manuell',
    quelle_ref TEXT NOT NULL DEFAULT '',
    quelle_link TEXT NOT NULL DEFAULT '',
    pruefen INTEGER NOT NULL DEFAULT 0,
    hinweise TEXT NOT NULL DEFAULT '[]',
    erechnung TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS belege_quelle ON belege(quelle, quelle_ref) WHERE quelle_ref != '';
CREATE TABLE IF NOT EXISTS umsaetze (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    konto TEXT NOT NULL,
    buchungsdatum TEXT NOT NULL,
    betrag TEXT NOT NULL,
    partner TEXT NOT NULL DEFAULT '',
    iban TEXT NOT NULL DEFAULT '',
    verwendungszweck TEXT NOT NULL DEFAULT '',
    typ TEXT NOT NULL DEFAULT '',
    original_betrag TEXT,
    original_waehrung TEXT NOT NULL DEFAULT '',
    pruefsumme TEXT NOT NULL UNIQUE,
    beleg_id INTEGER,
    ignoriert INTEGER NOT NULL DEFAULT 0,
    ignoriert_grund TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bank_regeln (
    partner TEXT PRIMARY KEY,
    grund TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sevdesk_import (
    objekt TEXT NOT NULL,
    sevdesk_id TEXT NOT NULL,
    status TEXT NOT NULL,
    paperless_task TEXT NOT NULL DEFAULT '',
    paperless_id TEXT NOT NULL DEFAULT '',
    belege TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (objekt, sevdesk_id)
);
CREATE TABLE IF NOT EXISTS kontakte (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rolle TEXT NOT NULL DEFAULT '',
    kundennummer TEXT NOT NULL DEFAULT '',
    ustid TEXT NOT NULL DEFAULT '',
    strasse TEXT NOT NULL DEFAULT '',
    plz TEXT NOT NULL DEFAULT '',
    ort TEXT NOT NULL DEFAULT '',
    land TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    telefon TEXT NOT NULL DEFAULT '',
    ansprechpartner TEXT NOT NULL DEFAULT '',
    notiz TEXT NOT NULL DEFAULT '',
    quellen TEXT NOT NULL DEFAULT '[]',
    aliase TEXT NOT NULL DEFAULT '[]',
    ausgeblendet INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kurse (
    waehrung TEXT NOT NULL,
    zeitraum TEXT NOT NULL,
    kurs TEXT NOT NULL,
    quelle TEXT NOT NULL,
    PRIMARY KEY (waehrung, zeitraum)
);
"""

FELDER = [
    "art", "steuerfall", "rechnungsdatum", "zahlungsdatum", "netto", "steuersatz", "steuer", "waehrung",
    "kurs", "partner", "partner_ustid", "rechnungsnummer", "beschreibung", "quelle", "quelle_ref",
    "quelle_link", "pruefen", "hinweise", "erechnung",
]


def _dec(v: str | None) -> Decimal | None:
    return Decimal(v) if v not in (None, "") else None


def _datum(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _zeile_zu_beleg(z: sqlite3.Row) -> Beleg:
    return Beleg(
        id=z["id"],
        art=Art(z["art"]),
        steuerfall=Steuerfall(z["steuerfall"]),
        rechnungsdatum=_datum(z["rechnungsdatum"]),
        zahlungsdatum=_datum(z["zahlungsdatum"]),
        netto=Decimal(z["netto"]),
        steuersatz=Decimal(z["steuersatz"]),
        steuer=_dec(z["steuer"]),
        waehrung=z["waehrung"],
        kurs=_dec(z["kurs"]),
        partner=z["partner"],
        partner_ustid=z["partner_ustid"],
        rechnungsnummer=z["rechnungsnummer"],
        beschreibung=z["beschreibung"],
        quelle=z["quelle"],
        quelle_ref=z["quelle_ref"],
        quelle_link=z["quelle_link"],
        pruefen=bool(z["pruefen"]),
        hinweise=json.loads(z["hinweise"]),
        erechnung=z["erechnung"],
    )


def _beleg_zu_werten(b: Beleg) -> list:
    def s(v):
        if v is None:
            return None
        if isinstance(v, (Decimal, int)) and not isinstance(v, bool):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        return v

    return [
        b.art.value, b.steuerfall.value, s(b.rechnungsdatum), s(b.zahlungsdatum), s(b.netto), s(b.steuersatz),
        s(b.steuer), b.waehrung.upper(), s(b.kurs), b.partner, b.partner_ustid, b.rechnungsnummer,
        b.beschreibung, b.quelle, b.quelle_ref, b.quelle_link, int(b.pruefen), json.dumps(b.hinweise),
        b.erechnung,
    ]


KONTAKT_FELDER = [
    "name", "rolle", "kundennummer", "ustid", "strasse", "plz", "ort", "land", "email", "telefon",
    "ansprechpartner", "notiz", "quellen", "aliase", "ausgeblendet",
]
JSON_FELDER = ("quellen", "aliase")


def _kontakt_zu_werten(k: Kontakt) -> list:
    return [json.dumps(getattr(k, f)) if f in JSON_FELDER else int(k.ausgeblendet) if f == "ausgeblendet"
            else getattr(k, f) for f in KONTAKT_FELDER]


def _zeile_zu_kontakt(z: sqlite3.Row) -> Kontakt:
    werte = {f: json.loads(z[f]) if f in JSON_FELDER else z[f] for f in KONTAKT_FELDER}
    return Kontakt(**{**werte, "ausgeblendet": bool(z["ausgeblendet"])}, id=z["id"])


UMSATZ_FELDER = [
    "konto", "buchungsdatum", "betrag", "partner", "iban", "verwendungszweck", "typ", "original_betrag",
    "original_waehrung", "pruefsumme", "beleg_id", "ignoriert", "ignoriert_grund",
]


def _umsatz_zu_werten(u: Umsatz) -> list:
    return [
        u.konto, u.buchungsdatum.isoformat(), str(u.betrag), u.partner, u.iban, u.verwendungszweck, u.typ,
        None if u.original_betrag is None else str(u.original_betrag), u.original_waehrung, u.pruefsumme,
        u.beleg_id, int(u.ignoriert), u.ignoriert_grund,
    ]


def _zeile_zu_umsatz(z: sqlite3.Row) -> Umsatz:
    return Umsatz(
        id=z["id"],
        konto=z["konto"],
        buchungsdatum=date.fromisoformat(z["buchungsdatum"]),
        betrag=Decimal(z["betrag"]),
        partner=z["partner"],
        iban=z["iban"],
        verwendungszweck=z["verwendungszweck"],
        typ=z["typ"],
        original_betrag=_dec(z["original_betrag"]),
        original_waehrung=z["original_waehrung"],
        pruefsumme=z["pruefsumme"],
        beleg_id=z["beleg_id"],
        ignoriert=bool(z["ignoriert"]),
        ignoriert_grund=z["ignoriert_grund"],
    )


class Datenbank:
    def __init__(self, pfad: Path | str):
        if str(pfad) != ":memory:":
            Path(pfad).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(pfad), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._con.executescript(SCHEMA)
            spalten = {z["name"] for z in self._con.execute("PRAGMA table_info(belege)")}
            if "erechnung" not in spalten:  # Datenbanken von vor den E-Rechnungen
                self._con.execute("ALTER TABLE belege ADD COLUMN erechnung TEXT NOT NULL DEFAULT ''")

    # Einstellungen aus der Oberfläche (überschreiben die Umgebungsvariablen)
    def einstellungen(self) -> dict[str, str]:
        with self._lock:
            return dict(self._con.execute("SELECT schluessel, wert FROM einstellungen").fetchall())

    def einstellung_setzen(self, schluessel: str, wert: str | None) -> None:
        """Speichert einen Wert; None löscht ihn (dann gilt wieder die Umgebungsvariable)."""
        with self._lock, self._con:
            if wert is None:
                self._con.execute("DELETE FROM einstellungen WHERE schluessel = ?", (schluessel,))
            else:
                self._con.execute(
                    "INSERT INTO einstellungen (schluessel, wert) VALUES (?, ?) "
                    "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
                    (schluessel, wert),
                )

    # Belege
    def alle_belege(self) -> list[Beleg]:
        with self._lock:
            zeilen = self._con.execute("SELECT * FROM belege ORDER BY rechnungsdatum, id").fetchall()
        return [_zeile_zu_beleg(z) for z in zeilen]

    def beleg(self, beleg_id: int) -> Beleg | None:
        with self._lock:
            z = self._con.execute("SELECT * FROM belege WHERE id = ?", (beleg_id,)).fetchone()
        return _zeile_zu_beleg(z) if z else None

    def beleg_vorhanden(self, quelle: str, quelle_ref: str) -> bool:
        with self._lock:
            z = self._con.execute(
                "SELECT 1 FROM belege WHERE quelle = ? AND quelle_ref = ?", (quelle, quelle_ref)
            ).fetchone()
        return z is not None

    def beleg_anlegen(self, b: Beleg) -> Beleg:
        platzhalter = ", ".join("?" for _ in FELDER)
        with self._lock, self._con:
            cur = self._con.execute(
                f"INSERT INTO belege ({', '.join(FELDER)}) VALUES ({platzhalter})", _beleg_zu_werten(b)
            )
        b.id = cur.lastrowid
        return b

    def beleg_aktualisieren(self, b: Beleg) -> Beleg:
        zuweisung = ", ".join(f"{f} = ?" for f in FELDER)
        with self._lock, self._con:
            self._con.execute(f"UPDATE belege SET {zuweisung} WHERE id = ?", [*_beleg_zu_werten(b), b.id])
        return b

    def beleg_loeschen(self, beleg_id: int) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM belege WHERE id = ?", (beleg_id,))
            self._con.execute("UPDATE umsaetze SET beleg_id = NULL WHERE beleg_id = ?", (beleg_id,))

    # Kontoumsätze
    def umsatz_anlegen(self, u: Umsatz) -> bool:
        """Legt den Umsatz an; False, wenn er (gleiche Prüfsumme) schon vorhanden ist."""
        with self._lock, self._con:
            cur = self._con.execute(
                f"INSERT OR IGNORE INTO umsaetze ({', '.join(UMSATZ_FELDER)}) "
                f"VALUES ({', '.join('?' for _ in UMSATZ_FELDER)})",
                _umsatz_zu_werten(u),
            )
        if cur.rowcount:
            u.id = cur.lastrowid
        return bool(cur.rowcount)

    def alle_umsaetze(self) -> list[Umsatz]:
        with self._lock:
            zeilen = self._con.execute("SELECT * FROM umsaetze ORDER BY buchungsdatum, id").fetchall()
        return [_zeile_zu_umsatz(z) for z in zeilen]

    def umsatz(self, umsatz_id: int) -> Umsatz | None:
        with self._lock:
            z = self._con.execute("SELECT * FROM umsaetze WHERE id = ?", (umsatz_id,)).fetchone()
        return _zeile_zu_umsatz(z) if z else None

    def umsatz_aktualisieren(self, u: Umsatz) -> Umsatz:
        zuweisung = ", ".join(f"{f} = ?" for f in UMSATZ_FELDER)
        with self._lock, self._con:
            self._con.execute(f"UPDATE umsaetze SET {zuweisung} WHERE id = ?", [*_umsatz_zu_werten(u), u.id])
        return u

    def bank_regeln(self) -> dict[str, str]:
        with self._lock:
            return {z["partner"]: z["grund"] for z in self._con.execute("SELECT * FROM bank_regeln")}

    def bank_regel_speichern(self, partner: str, grund: str) -> None:
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO bank_regeln (partner, grund) VALUES (?, ?) "
                "ON CONFLICT(partner) DO UPDATE SET grund = excluded.grund",
                (partner.strip().lower(), grund),
            )

    # Adressbuch
    def kontakte(self) -> list[Kontakt]:
        with self._lock:
            zeilen = self._con.execute("SELECT * FROM kontakte ORDER BY name COLLATE NOCASE, id").fetchall()
        return [_zeile_zu_kontakt(z) for z in zeilen]

    def kontakt(self, kontakt_id: int) -> Kontakt | None:
        with self._lock:
            z = self._con.execute("SELECT * FROM kontakte WHERE id = ?", (kontakt_id,)).fetchone()
        return _zeile_zu_kontakt(z) if z else None

    def kontakt_speichern(self, k: Kontakt) -> Kontakt:
        """Legt den Kontakt an (ohne id) oder überschreibt ihn."""
        with self._lock, self._con:
            if k.id is None:
                cur = self._con.execute(
                    f"INSERT INTO kontakte ({', '.join(KONTAKT_FELDER)}) "
                    f"VALUES ({', '.join('?' for _ in KONTAKT_FELDER)})", _kontakt_zu_werten(k),
                )
                k.id = cur.lastrowid
            else:
                zuweisung = ", ".join(f"{f} = ?" for f in KONTAKT_FELDER)
                self._con.execute(f"UPDATE kontakte SET {zuweisung} WHERE id = ?", [*_kontakt_zu_werten(k), k.id])
        return k

    def kontakt_loeschen(self, kontakt_id: int) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM kontakte WHERE id = ?", (kontakt_id,))

    # Kurse
    def kurs_lesen(self, waehrung: str, zeitraum: str) -> tuple[Decimal, str] | None:
        with self._lock:
            z = self._con.execute(
                "SELECT kurs, quelle FROM kurse WHERE waehrung = ? AND zeitraum = ?", (waehrung.upper(), zeitraum)
            ).fetchone()
        return (Decimal(z["kurs"]), z["quelle"]) if z else None

    def kurs_speichern(self, waehrung: str, zeitraum: str, kurs: Decimal, quelle: str) -> None:
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO kurse (waehrung, zeitraum, kurs, quelle) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(waehrung, zeitraum) DO UPDATE SET kurs = excluded.kurs, quelle = excluded.quelle",
                (waehrung.upper(), zeitraum, str(kurs), quelle),
            )

    def kurs_loeschen(self, waehrung: str, zeitraum: str) -> None:
        with self._lock, self._con:
            self._con.execute("DELETE FROM kurse WHERE waehrung = ? AND zeitraum = ?", (waehrung.upper(), zeitraum))

    def alle_kurse(self) -> list[dict]:
        with self._lock:
            zeilen = self._con.execute("SELECT * FROM kurse ORDER BY zeitraum DESC, waehrung").fetchall()
        return [dict(z) for z in zeilen]

    # Umzug aus sevDesk (siehe ustva/sevdesk.py): was schon übernommen wurde
    def sevdesk_importe(self) -> dict[tuple[str, str], dict]:
        with self._lock:
            zeilen = self._con.execute("SELECT * FROM sevdesk_import").fetchall()
        return {
            (z["objekt"], z["sevdesk_id"]): {**dict(z), "belege": json.loads(z["belege"])} for z in zeilen
        }

    def sevdesk_import_speichern(
        self, objekt: str, sevdesk_id: str, status: str, paperless_task: str = "", paperless_id: str = "",
        belege: list[int] | None = None,
    ) -> None:
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO sevdesk_import (objekt, sevdesk_id, status, paperless_task, paperless_id, belege) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(objekt, sevdesk_id) DO UPDATE SET status = excluded.status, "
                "paperless_task = excluded.paperless_task, paperless_id = excluded.paperless_id, "
                "belege = excluded.belege",
                (objekt, sevdesk_id, status, paperless_task, paperless_id, json.dumps(belege or [])),
            )
