from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from ustva import adressbuch, erechnung
from ustva.config import Config
from ustva.models import Beleg, Kontakt
from ustva.quellen import ImportErgebnis, speichern
from ustva.sevdesk_export import Export
from ustva.steuerfaelle import Art, Steuerfall
from ustva.web import create_app

from .erechnungen import CII_AUSGANG, UBL_EINGANG_RC
from .test_sevdesk_export import _export


def _beleg(partner, art=Art.EINGANG, ustid="", tag=1):
    return Beleg(art=art, steuerfall=Steuerfall.VORSTEUER_INLAND if art == Art.EINGANG else Steuerfall.INLAND,
                 rechnungsdatum=date(2026, 9, tag), netto=D("10"), partner=partner, partner_ustid=ustid)


def test_kontakte_aus_belegen(db):
    db.beleg_anlegen(_beleg("Hosting GmbH", tag=3))
    db.beleg_anlegen(_beleg("HOSTING GMBH.", tag=20))  # gleiche Firma, andere Schreibweise
    db.beleg_anlegen(_beleg("Cloud B.V.", ustid="NL853746333B01"))
    db.beleg_anlegen(_beleg("Cloud BV", ustid="NL 853746333B01"))  # über die USt-IdNr. erkannt
    db.beleg_anlegen(_beleg("Kunde GmbH", Art.AUSGANG))
    db.beleg_anlegen(_beleg("", Art.AUSGANG))

    liste = {z["name"]: z for z in adressbuch.liste(db)}
    assert set(liste) == {"Hosting GmbH", "Cloud B.V.", "Kunde GmbH"}
    assert (liste["Hosting GmbH"]["belege"], liste["Hosting GmbH"]["letzter_beleg"]) == (2, "2026-09-20")
    assert (liste["Cloud B.V."]["land"], liste["Cloud B.V."]["belege"]) == ("NL", 2)
    assert liste["Kunde GmbH"]["rolle"] == "kunde"

    # Von Hand Eingetragenes bleibt, ein zweiter Abgleich legt nichts doppelt an
    k = next(k for k in db.kontakte() if k.name == "Hosting GmbH")
    k.strasse, k.ort = "Hauptstr. 1", "Köln"
    db.kontakt_speichern(k)
    db.beleg_anlegen(_beleg("Hosting GmbH", Art.AUSGANG))
    a = adressbuch.aus_belegen(db)
    assert a.neu == 0
    k = db.kontakt(k.id)
    assert (k.strasse, k.ort, k.rolle) == ("Hauptstr. 1", "Köln", "beides")


def test_anschrift_nur_als_ganzes_ergaenzt(db):
    db.kontakt_speichern(Kontakt(name="Acme GmbH", ort="Berlin", quellen=["manuell"]))
    a = adressbuch.uebernehmen(db, [Kontakt(name="ACME GmbH", strasse="Weg 2", plz="80331", ort="München",
                                            email="rechnung@acme.example", quellen=["sevDesk"])])
    (k,) = db.kontakte()
    assert (a.neu, a.ergaenzt) == (0, 1)
    assert (k.strasse, k.plz, k.ort, k.email) == ("", "", "Berlin", "rechnung@acme.example")
    assert k.quellen == ["manuell", "sevDesk"]


def test_anschrift_aus_e_rechnungen(db):
    cii = CII_AUSGANG.replace(
        "<ram:Name>Kunde GmbH</ram:Name>\n        <ram:PostalTradeAddress><ram:CountryID>DE</ram:CountryID>",
        "<ram:Name>Kunde GmbH</ram:Name>\n        <ram:URIUniversalCommunication><ram:URIID schemeID=\"EM\">"
        "buchhaltung@kunde.example</ram:URIID></ram:URIUniversalCommunication>\n        <ram:PostalTradeAddress>"
        "<ram:PostcodeCode>50667</ram:PostcodeCode><ram:LineOne>Domplatz 1</ram:LineOne>"
        "<ram:CityName>Köln</ram:CityName><ram:CountryID>DE</ram:CountryID>",
    )
    belege = erechnung.belege(erechnung.xml_lesen(cii.encode()), Art.AUSGANG, "DE123456789")
    assert belege[0].anschrift == {"strasse": "Domplatz 1", "plz": "50667", "ort": "Köln", "land": "DE",
                                   "email": "buchhaltung@kunde.example"}
    speichern(db, belege, [], ImportErgebnis(), "rechnung.xml")
    (k,) = db.kontakte()
    assert (k.name, k.rolle, k.ustid, k.strasse, k.ort, k.quellen) == \
        ("Kunde GmbH", "kunde", "DE811111111", "Domplatz 1", "Köln", ["E-Rechnung"])

    ubl = UBL_EINGANG_RC.replace(
        "<cac:PostalAddress><cac:Country>",
        "<cac:PostalAddress><cbc:StreetName>Kade 5</cbc:StreetName><cbc:CityName>Amsterdam</cbc:CityName>"
        "<cbc:PostalZone>1011 AA</cbc:PostalZone><cac:Country>",
    )
    (b, *_) = erechnung.belege(erechnung.xml_lesen(ubl.encode()), Art.EINGANG, "DE123456789")
    assert b.anschrift == {"strasse": "Kade 5", "plz": "1011 AA", "ort": "Amsterdam", "land": "NL"}
    # Ohne Anschrift in der Rechnung bleibt sie leer
    assert erechnung.belege(erechnung.xml_lesen(CII_AUSGANG.encode()), Art.AUSGANG, "DE1")[0].anschrift == {}


def test_kontakte_aus_sevdesk_export(tmp_path):
    ordner = _export(tmp_path)
    (ordner / "contacts.csv").write_text(
        '﻿﻿"Kunden-Nr.";"Nachname";"Vorname";"Organisation";"Kategorie";"UmSt.-ID";"Strasse";"PLZ";"Ort";'
        '"Land";"E-Mail"\n'
        '"1000";"";"";"Acme GmbH";"Kunde";"DE 123456789";"";"";"";"Deutschland";""\n'
        '"1000";"Krüger";"Tobias";"Acme GmbH";"Kunde";"";"";"";"";"Deutschland";"tk@acme.example"\n'
        '"1041";"";"";"Cursor";"Lieferant";"";"801 West End Avenue";"10025";"New York";'
        '"Vereinigte Staaten von Amerika";""\n'
        '"1032";"";"";"OpenAI Ireland Limited";"Lieferant";"";"";"";"";"Deutschland";""\n',
        encoding="utf-8",
    )
    with (ordner / "invoices.csv").open("a", encoding="utf-8") as f:
        f.write("RE-1002;03.04.2024;02.05.2024;;Rechnung;100,00;119,00;119,00;1000;"
                "Acme GmbH Industriestr. 4 b 90402 Nürnberg;Deutschland;;\n")
    kontakte = {k.name: k for k in Export.laden(ordner).adressbuch()}
    acme = kontakte["Acme GmbH"]
    assert (acme.rolle, acme.kundennummer, acme.ustid, acme.ansprechpartner, acme.email) == \
        ("kunde", "1000", "DE123456789", "Tobias Krüger", "tk@acme.example")
    # Anschrift aus der Rechnung, weil der Kontakt keine hat
    assert (acme.strasse, acme.plz, acme.ort, acme.land) == ("Industriestr. 4 b", "90402", "Nürnberg", "DE")
    assert (kontakte["Cursor"].ort, kontakte["Cursor"].land) == ("New York", "US")
    # ohne Anschrift kein Land: sevDesk setzt dort „Deutschland“ auch bei ausländischen Anbietern
    assert kontakte["OpenAI Ireland Limited"].land == ""


def test_zusammenfuehren(db):
    db.beleg_anlegen(_beleg("Jetbrains"))
    db.beleg_anlegen(_beleg("JetBrains s.r.o.", ustid="CZ26502275"))
    adressbuch.aus_belegen(db)
    kurz, lang = sorted(db.kontakte(), key=lambda k: len(k.name))
    ziel = adressbuch.zusammenfuehren(db, lang.id, kurz.id)
    assert (ziel.aliase, ziel.ustid) == (["Jetbrains"], "CZ26502275")
    (z,) = adressbuch.liste(db)  # die alte Schreibweise wird nicht wieder angelegt und zählt mit
    assert (z["name"], z["belege"]) == ("JetBrains s.r.o.", 2)


def test_api(db, umrechner):
    c = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10)))
    db.beleg_anlegen(_beleg("Hosting GmbH"))
    assert [k["name"] for k in c.get("/api/kontakte").json()] == ["Hosting GmbH"]

    neu = c.post("/api/kontakte", json={"name": "Neukunde AG", "rolle": "kunde", "land": "Österreich",
                                        "ustid": "atu 12345678"})
    assert neu.status_code == 201
    assert (neu.json()["land"], neu.json()["ustid"], neu.json()["quellen"]) == ("AT", "ATU12345678", ["manuell"])
    assert c.post("/api/kontakte", json={"name": " "}).status_code == 422

    hosting = next(k for k in c.get("/api/kontakte").json() if k["name"] == "Hosting GmbH")
    geaendert = c.put(f"/api/kontakte/{hosting['id']}", json={"name": "Hosting Deutschland GmbH", "ort": "Bonn"})
    assert geaendert.json()["aliase"] == ["Hosting GmbH"]
    liste = {k["name"]: k for k in c.get("/api/kontakte").json()}
    assert liste["Hosting Deutschland GmbH"]["belege"] == 1  # Belege mit altem Namen zählen weiter
    assert "Hosting GmbH" not in liste

    assert c.delete(f"/api/kontakte/{hosting['id']}").status_code == 204
    assert [k["name"] for k in c.get("/api/kontakte").json()] == ["Neukunde AG"]
    csv = c.get("/api/kontakte/csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "Neukunde AG;kunde" in csv.text
