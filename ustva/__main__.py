"""Kommandozeile.

    python -m ustva serve                     Weboberfläche starten
    python -m ustva bericht                   Kennzahlen für die nächste fällige UStVA
    python -m ustva bericht --einlesen        vorher Verzeichnis/Paperless einlesen
    python -m ustva bericht --zeitraum 2026-09
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .berechnung import berechnen
from .config import Config
from .db import Datenbank
from .fristen import naechste_frist
from .quellen import paperless, verzeichnis
from .waehrung import Umrechner


def _euro(betrag, ganz: bool = False) -> str:
    text = f"{betrag:,.0f}" if ganz else f"{betrag:,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def bericht(args, cfg: Config) -> int:
    db = Datenbank(cfg.datenbank)
    frist = naechste_frist(date.today(), cfg.bundesland, cfg.dauerfristverlaengerung)
    zeitraum = args.zeitraum or frist.zeitraum
    jahr, monat = (int(t) for t in zeitraum.split("-"))
    if args.einlesen:
        if cfg.rechnungs_verzeichnis:
            e = verzeichnis.importieren(db, cfg)
            print(f"Verzeichnis: {e.neu} neu, {e.vorhanden} schon vorhanden, {len(e.fehler)} Fehler")
            for f in e.fehler:
                print(f"  ! {f}")
        if cfg.paperless_aktiv:
            e = paperless.importieren(db, cfg, jahr, monat)
            print(f"Paperless: {e.neu} neu, {e.vorhanden} schon vorhanden, {len(e.fehler)} Fehler")
            for f in e.fehler:
                print(f"  ! {f}")
        print()

    b = berechnen(db.alle_belege(), jahr, monat, Umrechner(db, cfg.ezb_kurse), cfg.versteuerung)
    print(f"UStVA {zeitraum} ({cfg.versteuerung.capitalize()}-Versteuerung)")
    if zeitraum == frist.zeitraum:
        print(f"Abgabe und Zahlung bis {frist.to_dict()['faellig_text']}")
    print()
    for p in b.posten:
        d = p.to_dict()
        zusatz = "  (berechnet ELSTER)" if d["von_elster_berechnet"] else ""
        kz = "" if d["ist_steuerzeile"] else f"Kz {d['kz']}"
        betrag = _euro(p.betrag, d["betrag_art"] == "bemessungsgrundlage")
        print(f"  {kz:<7} {betrag:>14} €  {d['titel']}{zusatz}")
    print()
    for h in b.hinweise:
        print(f"  [{h.stufe}] {h.text}")
    return 0 if b.vollstaendig else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ustva", description="Finanz-O-Mat: UStVA-Kennzahlen berechnen")
    sub = parser.add_subparsers(dest="befehl", required=True)
    s = sub.add_parser("serve", help="Weboberfläche starten")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("bericht", help="Kennzahlen ausgeben")
    r.add_argument("--zeitraum", help="JJJJ-MM, Standard: nächste fällige Voranmeldung")
    r.add_argument("--einlesen", action="store_true", help="vorher Belege aus Verzeichnis/Paperless einlesen")
    args = parser.parse_args(argv)

    cfg = Config.aus_umgebung()
    if args.befehl == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(cfg), host=args.host, port=args.port)
        return 0
    return bericht(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
