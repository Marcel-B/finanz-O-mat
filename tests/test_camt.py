# ruff: noqa: F811 (Fixtures aus test_kontoabruf als Parameter)
"""Alte Kontoauszüge im Format CAMT.053 (z. B. aus MoneyMoney) in die Kontenübersicht einlesen."""

from datetime import date
from decimal import Decimal as D

import pytest

from tests.test_kontoabruf import IBAN, JETZT, eb, konto, privat, schluessel  # noqa: F401 (Fixtures)
from ustva import camt
from ustva import kontoabruf as ka
from ustva.config import Config

FREMD_IBAN = "DE44500105175407324931"  # kein verbundenes Konto


def eintrag(betrag, richtung, tag, name="", iban="", zweck="", status="BOOK", ref="", mehr=""):
    rolle = "Dbtr" if richtung == "CRDT" else "Cdtr"
    partei = f"<{rolle}><Pty><Nm>{name}</Nm></Pty></{rolle}>" if name else ""
    konto_ = f"<{rolle}Acct><Id><IBAN>{iban}</IBAN></Id></{rolle}Acct>" if iban else ""
    return f"""
      <Ntry>
        <Amt Ccy="EUR">{betrag}</Amt>
        <CdtDbtInd>{richtung}</CdtDbtInd>
        <Sts><Cd>{status}</Cd></Sts>
        <BookgDt><Dt>{tag}</Dt></BookgDt>
        <ValDt><Dt>{tag}</Dt></ValDt>
        {f"<AcctSvcrRef>{ref}</AcctSvcrRef>" if ref else ""}
        <AddtlNtryInf>Lastschrift</AddtlNtryInf>
        <NtryDtls><TxDtls>
          {mehr}
          <RltdPties>{partei}{konto_}</RltdPties>
          <RmtInf><Ustrd>{zweck}</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>"""


def datei(*auszuege) -> bytes:
    stmts = "".join(f"""
    <Stmt>
      <Id>1</Id>
      <Acct><Id><IBAN>{iban}</IBAN></Id><Ccy>EUR</Ccy></Acct>
      {"".join(eintraege)}
    </Stmt>""" for iban, eintraege in auszuege)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <GrpHdr><MsgId>MM-1</MsgId><CreDtTm>2026-11-10T10:00:00</CreDtTm></GrpHdr>{stmts}
  </BkToCstmrStmt>
</Document>""".encode()


ALT = [
    eintrag("120.00", "DBIT", "2026-03-02", "Versicherung AG", "DE02120300000000202051", "SVWZ+Beitrag Maerz",
            ref="R-1"),
    eintrag("4522.00", "CRDT", "2026-03-28", "Beispiel AG", "DE02500105170137075030", "Rechnung 2026-012"),
    eintrag("3.80", "DBIT", "2026-04-01", "Café Kaffeebohne"),
    eintrag("3.80", "DBIT", "2026-04-01", "Café Kaffeebohne"),
    eintrag("9.99", "DBIT", "2026-04-02", "Vorgemerkt", status="PDNG"),
    eintrag("17.32", "DBIT", "2026-04-03", "OpenAI", zweck="Abo",
            mehr='<AmtDtls><InstdAmt><Amt Ccy="USD">20.00</Amt></InstdAmt></AmtDtls>'),
]


def test_lesen():
    (a,) = camt.lesen(datei((IBAN, ALT)))
    assert (a.iban, a.waehrung, len(a.umsaetze), a.vorgemerkt) == (IBAN, "EUR", 5, 1)
    versicherung, eingang, kaffee, _, openai = a.umsaetze
    assert (versicherung.betrag, versicherung.partner, versicherung.iban, versicherung.verwendungszweck) == (
        D("-120.00"), "Versicherung AG", "DE02120300000000202051", "Beitrag Maerz")
    assert (eingang.betrag, eingang.partner, eingang.buchungsdatum) == (D("4522.00"), "Beispiel AG",
                                                                        date(2026, 3, 28))
    assert (kaffee.verwendungszweck, kaffee.typ) == ("Lastschrift", "")
    assert (openai.original_betrag, openai.original_waehrung, openai.typ) == (D("-20.00"), "USD", "Lastschrift")


@pytest.mark.parametrize("inhalt, meldung", [
    (b"kein xml", "Keine lesbare XML-Datei"),
    (b"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:pain.001.001.09'><CstmrCdtTrfInitn/></Document>",
     "Keine CAMT.053-Datei"),
])
def test_falsche_datei(inhalt, meldung):
    with pytest.raises(camt.CamtFehler, match=meldung):
        camt.lesen(inhalt)


def test_einlesen_und_nochmal(db):
    db.bank_konto_speichern(konto(bezeichnung="Geschäftskonto"))
    trocken = camt.einlesen(db, datei((IBAN, ALT)), trocken=True)
    assert trocken[0].neu == 5 and not db.konto_buchungen(IBAN)

    (e,) = camt.einlesen(db, datei((IBAN, ALT)))
    assert (e.konto, e.neu, e.vorhanden, e.vorgemerkt, e.von, e.bis) == (
        "Geschäftskonto", 5, 0, 1, date(2026, 3, 2), date(2026, 4, 3))
    assert len(db.konto_buchungen(IBAN)) == 5  # beide gleiche Kaffees
    # Dieselbe Datei (und eine, die sich überschneidet) legt nichts doppelt an
    (e,) = camt.einlesen(db, datei((IBAN, ALT[1:])))
    assert (e.neu, e.vorhanden) == (0, 4)
    assert not db.umsaetze_von_bis("N26", date(2026, 1, 1), date(2026, 12, 31))  # nicht in die Buchhaltung


def test_unbekanntes_konto(db):
    db.bank_konto_speichern(konto())
    fremd, eigen = camt.einlesen(db, datei((FREMD_IBAN, ALT[:1]), (IBAN, ALT[:1])))
    assert "kein verbundenes Konto" in fremd.fehler and fremd.neu == 0
    assert eigen.neu == 1


def test_kein_doppel_mit_abruf(db, eb):
    """Abruf und Datei überschneiden sich: Abgerufen 59,50 am 12.09., in der Datei am 13.09."""
    db.bank_konto_speichern(konto(aktiv=False))
    ueberschneidung = [eintrag("59.50", "DBIT", "2026-09-13", "Hosting GmbH"),
                       eintrag("59.50", "DBIT", "2026-10-13", "Hosting GmbH")]
    # Erst die Datei, dann der Abruf: der Abruf übernimmt die eingelesene Zeile
    camt.einlesen(db, datei((IBAN, ueberschneidung)))
    ka.abrufen(db, Config(), eb, jetzt=JETZT)
    zeilen = db.konto_buchungen(IBAN)
    assert len(zeilen) == 7  # 6 abgerufene + der Oktober aus der Datei
    hosting = [z for z in zeilen if z["betrag"] == "-59.50"]
    assert sorted(z["pruefsumme"][:3] for z in hosting) == ["cam", "eb:"]
    # Die Datei noch einmal: weder die abgerufene noch die eingelesene Zeile kommt doppelt
    (e,) = camt.einlesen(db, datei((IBAN, ueberschneidung)))
    assert (e.neu, e.vorhanden) == (0, 2)
    assert len(db.konto_buchungen(IBAN)) == 7


def test_einschalten_nimmt_eingelesene_mit(db, eb):
    db.bank_konto_speichern(konto(aktiv=False))
    camt.einlesen(db, datei((IBAN, [eintrag("59.50", "DBIT", "2026-09-13", "Hosting GmbH"),
                                    eintrag("3.80", "DBIT", "2026-04-01", "Café Kaffeebohne"),
                                    eintrag("3.80", "DBIT", "2026-04-02", "Café Kaffeebohne")])))
    k = db.bank_konto(IBAN)
    k.aktiv = True
    db.bank_konto_speichern(k)
    a = ka.aus_uebersicht(db, Config(), k)
    assert a.neu == 3  # die zwei Kaffees an zwei Tagen bleiben zwei
    # Ein späterer Abruf übernimmt die eingelesene Hosting-Zeile in der Buchhaltung, statt sie zu verdoppeln
    e = ka.abrufen(db, Config(), eb, jetzt=JETZT)
    assert e.konten[0].aus_csv == 1
    buchhaltung = db.umsaetze_von_bis("N26", date(2026, 1, 1), date(2026, 12, 31))
    assert sum(1 for u in buchhaltung if u.betrag == D("-59.50")) == 1


def test_api_einlesen(db, eb):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ustva import konten_web

    app = FastAPI()
    app.include_router(konten_web.router(db, lambda: Config(), lambda c: eb, jetzt=lambda: JETZT))
    client = TestClient(app)
    db.bank_konto_speichern(konto())
    antwort = client.post("/api/konten/einlesen", content=datei((IBAN, ALT)))
    assert antwort.status_code == 200
    assert antwort.json()["auszuege"][0] | {"konto": None} == {
        "iban": IBAN, "konto": None, "neu": 5, "vorhanden": 0, "vorgemerkt": 1, "von": "2026-03-02",
        "bis": "2026-04-03", "fehler": ""}
    falsch = client.post("/api/konten/einlesen", content=b"MT940 :20:STARTUMS")
    assert falsch.status_code == 400 and "XML" in falsch.json()["detail"]


def test_befehl(db, tmp_path, capsys):
    import argparse

    from ustva.__main__ import bank_befehl

    db.bank_konto_speichern(konto(bezeichnung="Geschäftskonto"))
    pfad = tmp_path / "moneymoney.xml"
    pfad.write_bytes(datei((IBAN, ALT), (FREMD_IBAN, ALT[:1])))
    args = argparse.Namespace(schritt="einlesen", datei=[str(pfad)], trocken=False)
    assert bank_befehl(args, Config(), db) == 1  # ein Auszug ohne Konto
    ausgabe = capsys.readouterr()
    assert "Geschäftskonto" in ausgabe.out and "5 neu, 0 schon vorhanden, 1 vorgemerkt" in ausgabe.out
    assert "kein verbundenes Konto" in ausgabe.err
