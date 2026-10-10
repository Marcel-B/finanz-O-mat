"""Kommandozeile.

    python -m ustva serve                     Weboberfläche starten
    python -m ustva bericht                   Kennzahlen für die nächste fällige UStVA
    python -m ustva bericht --einlesen        vorher Verzeichnis/Paperless einlesen
    python -m ustva bericht --zeitraum 2026-09
    python -m ustva passwort                  Passwort der Weboberfläche neu setzen
    python -m ustva passwort --zuruecksetzen  wieder APP_BENUTZER/APP_PASSWORT verwenden
    python -m ustva sevdesk sichern --ordner DIR        alles aus sevDesk sichern (Token in SEVDESK_TOKEN)
    python -m ustva sevdesk importieren --ordner DIR    Sicherung übernehmen (erst mit --trocken)
    python -m ustva sevdesk abgleich --erwartet CSV     Zahllast je Monat gegen abgegebene Werte
    python -m ustva paperless duplikate [--loeschen]    doppelte Rechnungen in Paperless finden/aufräumen
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import einstellungen
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


def passwort(args, cfg: Config) -> int:
    from getpass import getpass

    anmeldung = einstellungen.Anmeldung(cfg, Datenbank(cfg.datenbank))
    if args.zuruecksetzen:
        anmeldung.zuruecksetzen()
        print("Passwort aus der App entfernt; es gelten wieder APP_BENUTZER und APP_PASSWORT.")
        return 0
    benutzer = args.benutzer or anmeldung.zugang()[0]
    neu = getpass(f"Neues Passwort für {benutzer}: ")
    if neu != getpass("Wiederholen: "):
        print("Die Passwörter stimmen nicht überein.", file=sys.stderr)
        return 1
    try:
        anmeldung.setzen(benutzer, neu)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Passwort für {benutzer} gespeichert. Ein laufender Dienst übernimmt es sofort.")
    return 0


def _liste(titel: str, eintraege: list[str], hoechstens: int = 30) -> None:
    if not eintraege:
        return
    print(f"{titel} ({len(eintraege)}):")
    for e in eintraege[:hoechstens]:
        print(f"  - {e}")
    if len(eintraege) > hoechstens:
        print(f"  … und {len(eintraege) - hoechstens} weitere")


def _vergleich_ausgeben(zeilen: list[dict]) -> int:
    print(f"{'Monat':<9} {'finanz-O-mat':>14} {'abgegeben':>14} {'Differenz':>12}")
    abweichungen = 0
    for z in zeilen:
        soll = "" if z["erwartet"] is None else _euro(z["erwartet"])
        diff = "" if z["differenz"] is None else _euro(z["differenz"])
        marke = "" if z["passt"] else "  <-- prüfen"
        if not z["vollstaendig"]:
            marke += "  (Kurs fehlt)"
        abweichungen += not z["passt"]
        print(f"{z['zeitraum']:<9} {_euro(z['zahllast']):>14} {soll:>14} {diff:>12}{marke}")
    if any(z["erwartet"] is not None for z in zeilen):
        print(f"\n{abweichungen} Monat(e) weichen um mehr als die Toleranz ab." if abweichungen
              else "\nAlle Monate passen.")
    return abweichungen


def duplikate_befehl(args, cfg: Config, db: Datenbank, eingabe=input, pl=None) -> int:
    from . import paperless_duplikate as pd

    if not cfg.paperless_aktiv:
        print("Paperless ist nicht eingerichtet (Einstellungen in der App oder PAPERLESS_URL/PAPERLESS_TOKEN).",
              file=sys.stderr)
        return 1
    pl = pl or paperless.PaperlessClient(cfg.paperless_url, cfg.paperless_token)
    try:
        gruppen = pd.finden(pl, cfg, db, args.von)
        tags = pl.namen("/api/tags/")
    except paperless.PaperlessFehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    if not gruppen:
        print("Keine doppelten Rechnungen gefunden.")
        return 0
    for gruppe in gruppen:
        print("\n".join(pd.beschreiben(gruppe, tags)) + "\n")
    machbar = [g for g in gruppen if not g.hinweis]
    anzahl = sum(len(g.loeschen) for g in machbar)
    print(f"{len(gruppen)} Rechnung(en) liegen mehrfach vor, {anzahl} Dokument(e) wären zu löschen.")
    if not args.loeschen:
        print("Zum Aufräumen erneut mit --loeschen aufrufen (fragt vorher noch einmal nach).")
        return 0
    if not machbar:
        return 0
    frage = f"{anzahl} Dokument(e) in Paperless löschen? [j/N] "
    if not args.ja and eingabe(frage).strip().lower()[:1] not in ("j", "y"):
        print("Nichts gelöscht.")
        return 0
    fehler = 0
    for gruppe in machbar:
        try:
            pd.aufraeumen(pl, gruppe)
        except paperless.PaperlessFehler as e:
            fehler += 1
            print(f"#{gruppe.behalten['id']}: {e}", file=sys.stderr)
    print(f"Aufgeräumt: {len(machbar) - fehler} Rechnung(en). Gelöschtes liegt im Papierkorb von Paperless.")
    return 1 if fehler else 0


def sevdesk_befehl(args, cfg: Config) -> int:
    from . import sevdesk

    ordner = Path(args.ordner) if getattr(args, "ordner", None) else None
    if args.schritt == "sichern":
        token = os.environ.get("SEVDESK_TOKEN", "")
        if not token:
            print("Bitte den API-Token in SEVDESK_TOKEN setzen (sevDesk: Einstellungen > Benutzer > API-Token).",
                  file=sys.stderr)
            return 1
        try:
            u = sevdesk.sichern(sevdesk.SevdeskClient(token), ordner, print)
        except sevdesk.SevdeskFehler as e:
            print(e, file=sys.stderr)
            return 1
        print(f"\nGesichert nach {ordner}: {len(u['dateien'])} Dateien, Kontakte in kontakte.csv")
        _liste("Ohne Datei in sevDesk", u["ohne_datei"])
        _liste("Fehler", u["fehler"])
        return 1 if u["fehler"] else 0

    db = Datenbank(cfg.datenbank)
    umrechner = Umrechner(db, cfg.ezb_kurse)
    erwartet = sevdesk.erwartet_lesen(Path(args.erwartet)) if args.erwartet else {}
    toleranz = Decimal(args.toleranz.replace(",", "."))
    if args.schritt == "abgleich":
        belege = db.alle_belege()
        monate = sorted(erwartet) if erwartet and not (args.von or args.bis) else \
            sevdesk.monate(belege, cfg.versteuerung, args.von, args.bis)
        zeilen = sevdesk.monatsvergleich(belege, umrechner, cfg.versteuerung, monate, erwartet, toleranz)
        return 2 if _vergleich_ausgeben(zeilen) else 0

    try:
        s = sevdesk.Sicherung.laden(ordner)
    except sevdesk.SevdeskFehler as e:
        print(e, file=sys.stderr)
        return 1
    erg = sevdesk.importieren(db, cfg, s, trocken=args.trocken, paperless_nutzen=not args.ohne_paperless,
                              von=args.von, bis=args.bis, warten=args.warten)
    print(("PROBELAUF – es wurde nichts geschrieben.\n" if args.trocken else "")
          + f"Sicherung vom {s.erstellt or '?'}: {erg.neu} {'würden übernommen' if args.trocken else 'übernommen'}"
          + f" ({len(erg.belege)} Belege), {erg.vorhanden} schon übernommen, {erg.ausserhalb} außerhalb des Zeitraums")
    if not args.trocken and not args.ohne_paperless:
        print(f"Paperless: {erg.hochgeladen} Dateien hochgeladen"
              + (f", {erg.wartet} noch in Verarbeitung (später einfach erneut starten)" if erg.wartet else ""))
    for grund, anzahl in sorted(erg.uebersprungen.items()):
        print(f"Nicht übernommen: {anzahl} × {grund}")
    print()
    _liste("Schon vorhanden, nicht doppelt angelegt", erg.doppelt)
    _liste("Hinweise an den Belegen", erg.pruefen)
    _liste("Ohne Datei (nur Buchung)", erg.ohne_datei, 10)
    _liste("Fehler", erg.fehler)
    if erg.belege:
        print()
        belege = db.alle_belege() + (erg.belege if args.trocken else [])
        monate = sevdesk.monate(erg.belege, cfg.versteuerung, args.von, args.bis)
        _vergleich_ausgeben(sevdesk.monatsvergleich(belege, umrechner, cfg.versteuerung, monate, erwartet, toleranz))
    return 1 if erg.fehler else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ustva", description="Finanz-O-Mat: UStVA-Kennzahlen berechnen")
    sub = parser.add_subparsers(dest="befehl", required=True)
    s = sub.add_parser("serve", help="Weboberfläche starten")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("bericht", help="Kennzahlen ausgeben")
    r.add_argument("--zeitraum", help="JJJJ-MM, Standard: nächste fällige Voranmeldung")
    r.add_argument("--einlesen", action="store_true", help="vorher Belege aus Verzeichnis/Paperless einlesen")
    p = sub.add_parser("passwort", help="Passwort der Weboberfläche setzen (z. B. wenn vergessen)")
    p.add_argument("--benutzer", help="Benutzername, Standard: der bisherige")
    p.add_argument("--zuruecksetzen", action="store_true", help="Passwort aus der App entfernen")
    sd = sub.add_parser("sevdesk", help="Umzug aus sevDesk (siehe docs/sevdesk-migration.md)")
    sd_sub = sd.add_subparsers(dest="schritt", required=True)
    s1 = sd_sub.add_parser("sichern", help="alle Daten und Dateien aus sevDesk in einen Ordner sichern")
    s1.add_argument("--ordner", required=True, help="Zielordner der Sicherung")
    s2 = sd_sub.add_parser("importieren", help="Sicherung nach finanz-O-mat und Paperless übernehmen")
    s2.add_argument("--ordner", required=True, help="Ordner der Sicherung")
    s2.add_argument("--trocken", action="store_true", help="Probelauf: nichts schreiben, nur zeigen")
    s2.add_argument("--ohne-paperless", action="store_true", help="Dateien nicht nach Paperless laden")
    s2.add_argument("--warten", type=float, default=300, help="Sekunden auf Paperless warten (Standard 300)")
    s3 = sd_sub.add_parser("abgleich", help="Zahllast je Monat gegen die abgegebenen Werte prüfen")
    for p_ in (s2, s3):
        p_.add_argument("--von", help="erster Monat JJJJ-MM")
        p_.add_argument("--bis", help="letzter Monat JJJJ-MM")
        p_.add_argument("--erwartet", help="CSV mit Zeitraum;Zahllast (z. B. aus ELSTER oder sevDesk)")
        p_.add_argument("--toleranz", default="1.00", help="erlaubte Abweichung in Euro (Standard 1,00)")
    pp = sub.add_parser("paperless", help="Paperless pflegen")
    pp_sub = pp.add_subparsers(dest="schritt", required=True)
    du = pp_sub.add_parser("duplikate", help="doppelte Rechnungen finden und auf Wunsch löschen")
    du.add_argument("--von", help="nur Dokumente ab Monat JJJJ-MM")
    du.add_argument("--loeschen", action="store_true", help="Duplikate löschen (fragt nach)")
    du.add_argument("--ja", action="store_true", help="beim Löschen nicht nachfragen")
    args = parser.parse_args(argv)

    cfg = Config.aus_umgebung()
    if args.befehl == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(cfg), host=args.host, port=args.port)
        return 0
    if args.befehl == "passwort":
        return passwort(args, cfg)
    if args.befehl == "paperless":
        db = Datenbank(cfg.datenbank)
        return duplikate_befehl(args, einstellungen.wirksam(cfg, db), db)
    if args.befehl == "sevdesk":
        return sevdesk_befehl(args, einstellungen.wirksam(cfg, Datenbank(cfg.datenbank)))
    return bericht(args, einstellungen.wirksam(cfg, Datenbank(cfg.datenbank)))


if __name__ == "__main__":
    sys.exit(main())
