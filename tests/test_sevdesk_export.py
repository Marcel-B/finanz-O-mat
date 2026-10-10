from datetime import date
from decimal import Decimal as D

import httpx

from ustva import sevdesk
from ustva.__main__ import main
from ustva.config import Config
from ustva.sevdesk_export import Export
from ustva.steuerfaelle import Art, Steuerfall
from ustva.waehrung import Umrechner

from .test_sevdesk import _paperless_transport

MD5 = "0123456789abcdef0123456789abcdef"

KOPF = '"Belegnummer";"Status";"Position ";"Lieferant / Kunde";"Kategorie";"Beschreibung";"Datum";"Fällig am";' \
       '"Bezahlt am";"offener Betrag";"Betrag";"Kostenstelle";"Umsatzsteuersatz";'


def _beleg(nummer, partner, datum, bezahlt, positionen, beschreibung=""):
    """Kopfzeile und Positionen wie im sevDesk-Export (Beträge brutto, deutsch geschrieben)."""
    summe = sum(D(b.replace(".", "").replace(",", ".")) for _, b, _ in positionen)
    zeilen = [f'"{nummer}";"bezahlt";"";"{partner}";"";"{beschreibung}";"{datum}";"{datum}";"{bezahlt}";"0,00";'
              f'"{str(summe).replace(".", ",")}";"";"";']
    for i, (kategorie, betrag, satz) in enumerate(positionen, 1):
        zeilen.append(f'"{nummer}";"";"{i}";"";"{kategorie}";"{beschreibung}";"";"";"";"";"{betrag}";"";"{satz}";')
    return zeilen


VOUCHER = [KOPF] + sum([
    # Inland 19 %, mit Datei
    _beleg("H-2024-03", "Hosting GmbH", "15.03.2024", "20.03.2024", [("Web-Hosting / Domains", "11,90", "19,00")]),
    # zwei Steuersätze auf einem Beleg
    _beleg("DB-7", "DB Fernverkehr AG", "02.03.2024", "02.03.2024",
           [("Bahn- / Flugticket, Mietwagen", "10,70", "7,00"), ("Bürobedarf", "1.190,00", "19,00")]),
    # 0 % bei EU-Anbieter (laut Name) und Drittland-Anbieter (laut Kontakt): Reverse Charge
    _beleg("OAI-1", "OpenAI Ireland Limited", "05.03.2024", "05.03.2024",
           [("laufende Gebühren für Lizenzen", "20,00", "0,00")]),
    _beleg("CUR-1", "Cursor", "06.03.2024", "06.03.2024", [("laufende Gebühren für Lizenzen", "18,40", "0,00")]),
    # 0 % im Inland: ohne Vorsteuer
    _beleg("Lohn 24-03", "Erika Muster", "28.03.2024", "28.03.2024", [("Löhne für Minijobs", "538,00", "0,00")]),
    # privat, zweimal mit gleicher Nummer am gleichen Tag
    _beleg("Privat", "Marcel", "10.03.2024", "10.03.2024", [("Privatentnahmen", "500,00", "0,00")]),
    _beleg("Privat", "Marcel", "10.03.2024", "10.03.2024", [("Privatentnahmen", "300,00", "0,00")]),
    # Einnahme (Gutschrift des Kunden), zählt bei Ist-Versteuerung im Monat der Zahlung
    _beleg("E-SB2024", "eightbit experts GmbH", "20.02.2024", "08.03.2024", [("Erlöse 19 % USt", "1.190,00", "19,00")]),
    # Belegdatum unplausibel weit vor der Zahlung
    _beleg("DRP1", "STRATO AG", "05.06.2016", "12.03.2024", [("Web-Hosting / Domains", "10,00", "19,00")]),
    # Zahlungen ans Finanzamt: Text vor Belegnummer, Belegnummer vor Zahlungsmonat
    _beleg("UStVA-03.2024", "STEUERVERWALTUNG NRW", "12.04.2024", "12.04.2024",
           [("Umsatzsteuer-Vorauszahlungen", "1.234,56", "0,00")], "Stnr 1/2/3 Umsatzsteuer Feb. 24 1.234,56"),
    _beleg("UStVA-03.2024", "Finanzamt", "10.04.2024", "10.04.2024",
           [("Umsatzsteuer-Vorauszahlungen", "99,00", "0,00")]),
    _beleg("UStVA Q4", "STEUERVERWALTUNG NRW", "15.01.2024", "15.01.2024",
           [("Umsatzsteuer-Vorauszahlungen, -Nachzahlungen, -Erstattungen", "600,00", "0,00")]),
    _beleg("Umst. 2406", "STEUERVERWALTUNG NRW", "13.07.2024", "13.07.2024",
           [("Umsatzsteuer-Vorauszahlungen, -Nachzahlungen, -Erstattungen", "50,00", "0,00")]),
    _beleg("Steuer Erstattung 2023", "STEUERVERWALTUNG NRW", "06.05.2024", "06.05.2024",
           [("Umsatzsteuer-Vorauszahlungen, -Nachzahlungen, -Erstattungen", "236,58", "0,00")],
           "StNr 1/2/3 Umsatzsteuer 23"),
], [])

INVOICES = """﻿"Rechnungs-Nr.";"Rechnungs-Datum";"Fällig am";"Bezahlt am";"Betreff";"Gesamtbetrag-Netto";\
"Gesamtbetrag-Brutto";"offener Betrag";"Empfänger-KdNr";"Empfänger-Adresse";"Empfänger-Land";"IBAN";"BIC"
RE-1000;03.02.2024;02.03.2024;04.03.2024;Rechnung Nr. RE-1000;1000,00;1190,00;0,00;1000;;Deutschland;;
RE-1001;03.03.2024;02.04.2024;;Rechnung Nr. RE-1001;500,00;595,00;595,00;1000;;Deutschland;;
"""

KONTAKTE = """﻿﻿"Kunden-Nr.";"Organisation";"Vorname";"Nachname";"Kategorie";"UmSt.-ID";"Land"
"1000";"Acme GmbH";"";"";"Kunde";"DE123456789";"Deutschland"
"1041";"Cursor";"";"";"Lieferant";"";"Vereinigte Staaten von Amerika"
"""


def _export(tmp_path):
    ordner = tmp_path / "sevDesk"
    (ordner / "Belege_11").mkdir(parents=True)
    (ordner / "Rechnungen_11").mkdir()
    (ordner / "voucher.csv").write_text("﻿" + "\n".join(VOUCHER) + "\n", encoding="utf-8")
    (ordner / "invoices.csv").write_text(INVOICES, encoding="utf-8")
    (ordner / "contacts.csv").write_text(KONTAKTE, encoding="utf-8")
    for name in ("2024-03-15_Ausgabebeleg_H-2024-03", "2024-03-05_Ausgabebeleg_OAI-1",
                 "2024-03-10_Ausgabebeleg_Privat", "2024-02-20_Einnahmebeleg_E-SB2024"):
        (ordner / "Belege_11" / f"{name}_{MD5}.pdf").write_bytes(b"%PDF-1.4 beleg")
    (ordner / "Rechnungen_11" / f"2024-02-03_Rechnung_RE-1000_{MD5}.pdf").write_bytes(b"%PDF-1.4 rechnung")
    return ordner


def _kandidaten(tmp_path):
    return {(k.objekt, k.sevdesk_id): k for k in Export.laden(_export(tmp_path)).kandidaten()}


def test_export_abbildung(tmp_path):
    k = _kandidaten(tmp_path)

    hosting = k[("Voucher", "2024-03-15_h202403")]
    assert hosting.datei is not None and hosting.datei.name.startswith("2024-03-15_Ausgabebeleg_H-2024-03")
    (b,) = hosting.belege
    assert (b.art, b.steuerfall, b.netto, b.steuer, b.steuersatz) == \
        (Art.EINGANG, Steuerfall.VORSTEUER_INLAND, D("10.00"), D("1.90"), D(19))
    assert (b.rechnungsdatum, b.zahlungsdatum, b.rechnungsnummer) == (date(2024, 3, 15), date(2024, 3, 20), "H-2024-03")

    bahn = k[("Voucher", "2024-03-02_db7")]
    assert [(b.steuersatz, b.netto, b.steuer) for b in bahn.belege] == \
        [(D(19), D("1000.00"), D("190.00")), (D(7), D("10.00"), D("0.70"))]
    assert bahn.datei is None

    (oai,) = k[("Voucher", "2024-03-05_oai1")].belege
    assert (oai.steuerfall, oai.netto, oai.steuersatz, oai.steuer) == \
        (Steuerfall.EU_LEISTUNG_13B, D("20.00"), D(19), None)
    assert "Reverse Charge" in oai.hinweise[0]
    (cursor,) = k[("Voucher", "2024-03-06_cur1")].belege
    assert cursor.steuerfall == Steuerfall.AUSLAND_13B  # Land aus contacts.csv

    (lohn,) = k[("Voucher", "2024-03-28_lohn2403")].belege
    assert (lohn.steuerfall, lohn.steuer, lohn.hinweise) == (Steuerfall.OHNE_VORSTEUER, D(0), [])

    privat = [k[("Voucher", "2024-03-10_privat")], k[("Voucher", "2024-03-10_privat-2")]]
    assert all("Privat" in p.grund for p in privat)
    assert privat[0].datei is not None and privat[1].datei is None  # eine Datei, der Reihe nach vergeben

    erloes = k[("Voucher", "2024-02-20_esb2024")]
    (e,) = erloes.belege
    assert (e.art, e.steuerfall, e.netto, e.steuer, e.zahlungsdatum) == \
        (Art.AUSGANG, Steuerfall.INLAND, D("1000.00"), D("190.00"), date(2024, 3, 8))

    (strato,) = k[("Voucher", "2016-06-05_drp1")].belege
    assert strato.rechnungsdatum == date(2016, 6, 5)  # wie in sevDesk, aber mit Hinweis
    assert "über ein Jahr vor der Zahlung" in strato.hinweise[0]

    assert "Umsatzsteuer-Zahlung" in k[("Voucher", "2024-04-12_ustva032024")].grund

    (re1000,) = k[("Invoice", "2024-02-03_re1000")].belege
    assert (re1000.art, re1000.steuerfall, re1000.netto, re1000.steuer, re1000.steuersatz) == \
        (Art.AUSGANG, Steuerfall.INLAND, D("1000.00"), D("190.00"), D(19))
    assert (re1000.partner, re1000.partner_ustid, re1000.zahlungsdatum) == \
        ("Acme GmbH", "DE123456789", date(2024, 3, 4))
    assert k[("Invoice", "2024-02-03_re1000")].datei is not None
    (re1001,) = k[("Invoice", "2024-03-03_re1001")].belege
    assert re1001.zahlungsdatum is None  # offen: zählt bei Ist-Versteuerung noch nicht


def test_export_abgegebene_zahllast(tmp_path):
    werte, offen = Export.laden(_export(tmp_path)).abgegeben()
    assert werte == {
        "2024-02": D("1234.56"),  # Text der Überweisung schlägt die Belegnummer
        "2024-03": D("99.00"),  # Belegnummer
        "2023-Q4": D("600.00"),  # Quartal aus der Belegnummer, Jahr aus dem Zahlungsmonat
        "2024-06": D("50.00"),  # Monat vor der Zahlung
    }
    assert offen == ["Steuer Erstattung 2023 vom 06.05.2024: 236.58 €"]


def test_export_import_ist_wiederholbar(tmp_path, db):
    export = Export.laden(_export(tmp_path))
    erg = sevdesk.importieren(db, Config(), export, trocken=True)
    assert (erg.neu, len(erg.belege), db.alle_belege()) == (9, 10, [])
    assert erg.uebersprungen["Privat bzw. Einkommensteuer (gehört nicht in die UStVA)"] == 2

    erg = sevdesk.importieren(db, Config(), export, paperless_nutzen=False)
    assert (erg.neu, len(db.alle_belege())) == (9, 10)
    assert {b.quelle for b in db.alle_belege()} == {"sevdesk"}
    assert not any(b.pruefen for b in db.alle_belege())
    assert sevdesk.importieren(db, Config(), export, paperless_nutzen=False).vorhanden == 9


def test_export_import_mit_paperless(tmp_path, db):
    export = Export.laden(_export(tmp_path))
    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    protokoll: dict = {}
    erg = sevdesk.importieren(db, cfg, export, client=httpx.Client(transport=_paperless_transport(protokoll)),
                              schlafen=lambda _: None)
    assert erg.fehler == []
    assert erg.hochgeladen == 4  # Hosting, OpenAI, Erlös, RE-1000 (Privat wird nicht übernommen)
    assert sorted(protokoll["tags"]) == ["Ausgangsrechnung", "sevDesk"]
    hosting = next(b for b in db.alle_belege() if b.rechnungsnummer == "H-2024-03")
    assert hosting.quelle == "paperless"


def test_monatsvergleich_mit_quartal(tmp_path, db):
    export = Export.laden(_export(tmp_path))
    erg = sevdesk.importieren(db, Config(), export, trocken=True)
    umrechner = Umrechner(db, ezb_abrufen=False, heute=date(2026, 10, 10))
    erwartet = {"2024-Q1": D("100"), "2024-04": D("0")}
    zeilen = sevdesk.monatsvergleich(erg.belege, umrechner, "ist", sevdesk.monate(erg.belege, "ist", von="2024-01"),
                                     erwartet)
    q1 = zeilen[0]
    assert q1["zeitraum"] == "2024-Q1"
    # März (Ist): USt 190 (Erlös) + 190 (RE-1000); Vorsteuer 1,90 + 190 + 0,70; Reverse Charge gleicht sich aus;
    # Januar und Februar ohne Belege
    assert q1["zahllast"] == D("380") - D("1.90") - D("190.00") - D("0.70")
    assert [z["zeitraum"] for z in zeilen] == ["2024-Q1"]
    assert sevdesk.monate_von(erwartet) == ["2024-01", "2024-02", "2024-03", "2024-04"]


def test_erwartet_lesen_quartale(tmp_path):
    datei = tmp_path / "ustva.csv"
    datei.write_text("Zeitraum;Zahllast\n2023-Q4;600,00\nQ1/2023;1,00\n", encoding="utf-8")
    assert sevdesk.erwartet_lesen(datei) == {"2023-Q4": D("600.00"), "2023-Q1": D("1.00")}


def test_cli_probelauf_mit_export(tmp_path, monkeypatch, capsys):
    ordner = _export(tmp_path)
    monkeypatch.setenv("DATENBANK", str(tmp_path / "f.sqlite"))
    monkeypatch.setenv("EZB_KURSE", "0")
    assert main(["sevdesk", "importieren", "--ordner", str(ordner), "--trocken"]) == 0
    aus = capsys.readouterr().out
    assert "Export vom" in aus and "9 würden übernommen (10 Belege)" in aus
    assert "Abgegebene Zahllast aus den Umsatzsteuer-Zahlungen im Export (4 Zeiträume)" in aus
    assert "2023-Q4" not in aus.split("Monat ")[-1]  # keine Belege im Quartal, kein Vergleich im Probelauf

    assert main(["sevdesk", "abgleich", "--ordner", str(ordner)]) == 2  # noch nichts übernommen
    assert "2023-Q4" in capsys.readouterr().out
