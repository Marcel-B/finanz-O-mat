from datetime import date
from decimal import Decimal as D

import httpx
import pytest

from ustva import erechnung
from ustva.berechnung import berechnen
from ustva.config import Config
from ustva.quellen import paperless, verzeichnis
from ustva.steuerfaelle import Art, Steuerfall

from .erechnungen import CII_AUSGANG, CII_MINIMUM, UBL_EINGANG_RC, UBL_GUTSCHRIFT, zugferd_pdf


def test_cii_mit_zwei_steuersaetzen():
    r = erechnung.xml_lesen(CII_AUSGANG.encode())
    assert r.format == "E-Rechnung EN 16931 (CII)"
    assert (r.rechnungsnummer, r.rechnungsdatum) == ("2026-041", date(2026, 9, 15))
    assert (r.kaeufer.name, r.kaeufer.ustid) == ("Kunde GmbH", "DE811111111")

    b19, b7 = erechnung.belege(r, Art.AUSGANG, "DE123456789")
    assert b19.steuerfall == b7.steuerfall == Steuerfall.INLAND
    assert (b19.netto, b19.steuersatz, b19.steuer) == (D("1000.00"), D("19.00"), D("190.00"))
    assert (b7.netto, b7.steuersatz, b7.steuer) == (D("50.00"), D("7.00"), D("3.50"))
    assert b19.partner == "Kunde GmbH"
    assert not b19.pruefen and not b19.hinweise


def test_xrechnung_ubl_reverse_charge(umrechner):
    r = erechnung.xml_lesen(UBL_EINGANG_RC.encode())
    assert r.format == "XRechnung (UBL)"
    (b,) = erechnung.belege(r, Art.EINGANG, "DE123456789")
    assert b.steuerfall == Steuerfall.EU_LEISTUNG_13B
    assert (b.partner, b.partner_ustid, b.netto) == ("Cloud B.V.", "NL853746333B01", D("120.00"))
    assert (b.steuersatz, b.steuer) == (D(19), None)
    assert b.pruefen  # 19 % ist eine Annahme

    bericht = berechnen([b], 2026, 9, umrechner, "ist")
    assert bericht.wert("46") == D("120")
    assert bericht.wert("47") == bericht.wert("67") == D("22.80")


def test_gutschrift_wird_negativ():
    (b,) = erechnung.belege(erechnung.xml_lesen(UBL_GUTSCHRIFT.encode()), Art.EINGANG)
    assert (b.steuerfall, b.netto, b.steuer) == (Steuerfall.VORSTEUER_INLAND, D("-10.00"), D("-1.90"))
    assert any("Gutschrift" in h for h in b.hinweise)


def test_minimum_profil_ohne_aufschluesselung():
    r = erechnung.xml_lesen(CII_MINIMUM.encode())
    (b,) = erechnung.belege(r, Art.EINGANG)
    assert r.format == "E-Rechnung Minimum (CII)"
    assert b.steuerfall == Steuerfall.VORSTEUER_INLAND
    assert (b.netto, b.steuersatz, b.steuer) == (D("40.00"), D(19), D("7.60"))
    assert b.pruefen


def test_zugferd_pdf_und_falsche_ablage():
    r = erechnung.lesen(zugferd_pdf(CII_AUSGANG))
    assert r.format == "ZUGFeRD/Factur-X EN 16931"
    belege = erechnung.belege(r, Art.EINGANG, "DE123456789")
    assert all(b.pruefen for b in belege)
    assert "Verkäufer" in belege[0].hinweise[0]


def test_auslaendische_steuer_wird_kosten():
    xml = UBL_GUTSCHRIFT.replace("DE987654321", "FR11999999998").replace("CreditNote", "Invoice")
    xml = xml.replace("<cbc:InvoiceTypeCode>381", "<cbc:InvoiceTypeCode>380")
    (b,) = erechnung.belege(erechnung.xml_lesen(xml.encode()), Art.EINGANG)
    assert (b.steuerfall, b.netto, b.steuer) == (Steuerfall.OHNE_VORSTEUER, D("11.90"), D(0))
    assert b.pruefen


def test_zugferd_1_im_pdf_faellt_auf_text_zurueck(tmp_path, db):
    (tmp_path / "eingang").mkdir()
    alt = '<rsm:CrossIndustryDocument xmlns:rsm="urn:ferd:CrossIndustryDocument:invoice:1p0"/>'
    (tmp_path / "eingang" / "alt.pdf").write_bytes(zugferd_pdf(alt, "ZUGFeRD-invoice.xml"))
    ergebnis = verzeichnis.importieren(db, Config(rechnungs_verzeichnis=tmp_path))
    assert (ergebnis.neu, ergebnis.fehler) == (1, [])
    (b,) = db.alle_belege()
    assert "ZUGFeRD 1.0" in b.hinweise[0] and b.pruefen and not b.erechnung


def test_kein_e_rechnungs_xml():
    with pytest.raises(erechnung.ERechnungFehler):
        erechnung.xml_lesen(b"<html/>")
    with pytest.raises(erechnung.ERechnungFehler):
        erechnung.xml_lesen(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "a">]><Invoice/>')
    assert erechnung.lesen(zugferd_pdf("<notiz/>", "notiz.xml")) is None


def test_verzeichnis_import_e_rechnungen(tmp_path, db):
    (tmp_path / "ausgang").mkdir()
    (tmp_path / "eingang").mkdir()
    (tmp_path / "ausgang" / "re-041.pdf").write_bytes(zugferd_pdf(CII_AUSGANG))
    (tmp_path / "eingang" / "cloud.xml").write_text(UBL_EINGANG_RC)
    (tmp_path / "eingang" / "kaputt.xml").write_text("<Invoice><cbc:ID>")
    cfg = Config(rechnungs_verzeichnis=tmp_path, eigene_ustid="DE123456789")

    ergebnis = verzeichnis.importieren(db, cfg)
    assert (ergebnis.neu, len(ergebnis.fehler)) == (2, 1)
    assert "kaputt.xml" in ergebnis.fehler[0]
    belege = db.alle_belege()
    assert len(belege) == 3
    ausgang = [b for b in belege if b.art == Art.AUSGANG]
    assert {b.erechnung for b in ausgang} == {"ZUGFeRD/Factur-X EN 16931"}
    assert ausgang[0].quelle_ref + "#2" == ausgang[1].quelle_ref
    assert ausgang[0].quelle_link == "ausgang/re-041.pdf"

    # zweiter Lauf: nichts doppelt
    assert verzeichnis.importieren(db, cfg).vorhanden == 2
    assert len(db.alle_belege()) == 3


def test_paperless_original_mit_zugferd(db):
    pdf = zugferd_pdf(UBL_EINGANG_RC, "xrechnung.xml")
    dok = {"id": 31, "title": "Cloud September", "tags": [1], "document_type": None, "correspondent": None,
           "created": "2026-09-17", "content": "OCR-Text ohne Beträge", "custom_fields": [],
           "original_file_name": "cloud-2026-09.pdf", "mime_type": "application/pdf"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/documents/31/download/":
            assert request.url.params["original"] == "true"
            return httpx.Response(200, content=pdf)
        if request.url.path == "/api/documents/":
            return httpx.Response(200, json={"next": None, "results": [dok]})
        if request.url.path == "/api/tags/":
            return httpx.Response(200, json={"next": None, "results": [{"id": 1, "name": "Eingangsrechnung"}]})
        return httpx.Response(200, json={"next": None, "results": []})

    cfg = Config(paperless_url="http://paperless.local", paperless_token="geheim")
    ergebnis = paperless.importieren(db, cfg, 2026, 9, httpx.Client(transport=httpx.MockTransport(handler)))
    assert (ergebnis.neu, ergebnis.fehler) == (1, [])
    (b,) = db.alle_belege()
    assert (b.steuerfall, b.netto, b.partner) == (Steuerfall.EU_LEISTUNG_13B, D("120.00"), "Cloud B.V.")
    assert (b.quelle, b.quelle_ref, b.erechnung) == ("paperless", "31", "ZUGFeRD/Factur-X XRechnung")
    assert b.beschreibung == "Cloud September"


def test_alte_datenbank_bekommt_spalte(tmp_path):
    import sqlite3

    from ustva.db import Datenbank

    pfad = tmp_path / "alt.sqlite"
    con = sqlite3.connect(pfad)
    con.execute("CREATE TABLE belege (id INTEGER PRIMARY KEY, art TEXT, steuerfall TEXT, rechnungsdatum TEXT, "
                "zahlungsdatum TEXT, netto TEXT, steuersatz TEXT, steuer TEXT, waehrung TEXT, kurs TEXT, "
                "partner TEXT, partner_ustid TEXT, rechnungsnummer TEXT, beschreibung TEXT, quelle TEXT, "
                "quelle_ref TEXT NOT NULL DEFAULT '', quelle_link TEXT, pruefen INTEGER, hinweise TEXT)")
    con.execute("INSERT INTO belege VALUES (1, 'eingang', 'vorsteuer_inland', '2026-09-01', NULL, '10', '19', "
                "NULL, 'EUR', NULL, '', '', '', '', 'manuell', '', '', 0, '[]')")
    con.commit()
    con.close()
    (b,) = Datenbank(pfad).alle_belege()
    assert b.erechnung == ""
