"""Ablage der Belege und Kurse in SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Beleg
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
    hinweise TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS belege_quelle ON belege(quelle, quelle_ref) WHERE quelle_ref != '';
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
    "quelle_link", "pruefen", "hinweise",
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
    ]


class Datenbank:
    def __init__(self, pfad: Path | str):
        if str(pfad) != ":memory:":
            Path(pfad).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(pfad), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._con.executescript(SCHEMA)

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
