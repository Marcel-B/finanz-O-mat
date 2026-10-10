import base64
import json
from datetime import date
from decimal import Decimal as D

import httpx

from ustva import sevdesk
from ustva.config import Config
from ustva.models import Beleg
from ustva.steuerfaelle import Art, Steuerfall
from ustva.waehrung import Umrechner

PDF = b"%PDF-1.4 beleg"


def _ref(objekt: str, i) -> dict:
    return {"id": str(i), "objectName": objekt}


KONTAKTE = [
    {"id": "1", "name": "Hosting GmbH", "customerNumber": "", "vatNumber": "DE111111111"},
    {"id": "2", "name": "JetBrains s.r.o.", "vatNumber": "CZ26502275"},
    {"id": "3", "name": "Acme AG", "customerNumber": "1001", "vatNumber": "DE222222222"},
    {"id": "4", "surename": "Erika", "familyname": "Muster"},
]
VOUCHERS = [
    # Inland mit 19 % und 7 % auf einem Beleg
    {"id": "10", "voucherDate": "2024-03-15T00:00:00+01:00", "payDate": "2024-03-20T00:00:00+01:00",
     "status": "1000", "creditDebit": "C", "voucherType": "VOU", "taxRule": _ref("TaxRule", 9),
     "supplier": _ref("Contact", 1), "supplierNameAtSave": "Hosting GmbH", "description": "H-2024-03",
     "sumNet": "110.00", "sumTax": "16.30", "currency": "EUR", "document": _ref("Document", 99)},
    # EU-Anbieter mit deutscher USt: wie in sevDesk Vorsteuer Kz 66
    {"id": "11", "voucherDate": "2024-03-02", "status": "1000", "creditDebit": "C", "voucherType": "VOU",
     "taxRule": _ref("TaxRule", 9), "supplier": _ref("Contact", 2), "description": "JB-1",
     "sumNet": "100.00", "sumTax": "19.00", "document": _ref("Document", 98)},
    # OpenAI in USD, Reverse Charge Drittland
    {"id": "12", "voucherDate": "2024-03-05", "status": "1000", "creditDebit": "C", "voucherType": "VOU",
     "taxRule": _ref("TaxRule", 12), "supplierName": "OpenAI", "description": "OAI-7",
     "sumNet": "18.40", "sumTax": "0", "currency": "USD", "propertyExchangeRate": "1.0870",
     "document": None},
    # Entwurf und Vorlage werden nicht übernommen
    {"id": "13", "voucherDate": "2024-03-06", "status": "50", "creditDebit": "C", "taxRule": _ref("TaxRule", 9),
     "sumNet": "5", "sumTax": "0.95"},
    {"id": "14", "voucherDate": "2024-03-06", "status": "100", "creditDebit": "C", "voucherType": "RV",
     "taxRule": _ref("TaxRule", 9), "sumNet": "5", "sumTax": "0.95"},
    # alter Beleg von vor sevdesk-Update 2.0 (taxType statt Steuerregel)
    {"id": "15", "voucherDate": "15.01.2023", "status": "1000", "creditDebit": "C", "taxType": "default",
     "supplierName": "Bürobedarf", "description": "BB-1", "sumNet": "10.00", "sumTax": "1.90"},
]
VOUCHER_POS = [
    {"id": "100", "voucher": _ref("Voucher", 10), "taxRate": "19", "sumNet": "100.00", "sumTax": "19.00",
     "comment": "Server"},
    {"id": "101", "voucher": _ref("Voucher", 10), "taxRate": "7", "sumNet": "10.00", "sumTax": "0.70",
     "comment": "Buch"},
    {"id": "102", "voucher": _ref("Voucher", 11), "taxRate": "19.0", "sumNet": "100.00", "sumTax": "19.00"},
    {"id": "103", "voucher": _ref("Voucher", 12), "taxRate": "0", "sumNet": "18.40", "sumTax": "0"},
]
INVOICES = [
    {"id": "20", "invoiceNumber": "RE-1001", "invoiceDate": "2024-02-28", "payDate": "2024-03-10",
     "status": "1000", "invoiceType": "RE", "taxRule": _ref("TaxRule", 1), "contact": _ref("Contact", 3),
     "addressName": "Acme AG", "sumNet": "1000.00", "sumTax": "190.00"},
    {"id": "21", "invoiceNumber": "RE-1002", "invoiceDate": "2024-03-30", "status": "200", "invoiceType": "RE",
     "taxRule": _ref("TaxRule", 1), "contact": _ref("Contact", 3), "sumNet": "500.00", "sumTax": "95.00"},
    {"id": "22", "invoiceNumber": "SR-1001", "invoiceDate": "2024-03-12", "payDate": "2024-03-12",
     "status": "1000", "invoiceType": "SR", "taxRule": _ref("TaxRule", 1), "contact": _ref("Contact", 3),
     "sumNet": "100.00", "sumTax": "19.00"},
    {"id": "23", "invoiceNumber": "", "invoiceDate": "2024-03-30", "status": "100", "invoiceType": "RE",
     "taxRule": _ref("TaxRule", 1), "sumNet": "1", "sumTax": "0.19"},
]


def _sevdesk_transport(anfragen: list | None = None, seite: int = 1000):
    daten = {
        "Contact": KONTAKTE, "Voucher": VOUCHERS, "VoucherPos": VOUCHER_POS, "Invoice": INVOICES,
        "ContactAddress": [{"id": "1", "contact": _ref("Contact", 3), "street": "Weg 1", "zip": "12345",
                            "city": "Musterstadt"}],
        "CommunicationWay": [{"id": "1", "contact": _ref("Contact", 3), "type": "EMAIL", "value": "buch@acme.de"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if anfragen is not None:
            anfragen.append(request)
        assert request.headers["Authorization"] == "sd-token"
        pfad = request.url.path.removeprefix("/api/v1/")
        if pfad.endswith("/downloadDocument"):
            vid = pfad.split("/")[1]
            if vid == "11":
                return httpx.Response(200, json={"objects": None})
            return httpx.Response(200, json={"objects": {
                "filename": "hosting.pdf", "mimeType": "application/pdf", "base64Encoded": True,
                "content": base64.b64encode(PDF).decode()}})
        if pfad.endswith("/getPdf"):
            assert request.url.params["preventSendBy"] == "true"
            return httpx.Response(200, json={"filename": "RE.pdf", "base64encoded": True,
                                             "content": base64.b64encode(PDF + pfad.encode()).decode()})
        offset = int(request.url.params["offset"])
        liste = daten.get(pfad, [])
        # sevDesk liefert hier weniger als verlangt: trotzdem muss alles ankommen
        return httpx.Response(200, json={"objects": liste[offset:offset + seite]})

    return httpx.MockTransport(handler)


def _sichern(tmp_path, seite=1000):
    client = sevdesk.SevdeskClient("sd-token", httpx.Client(transport=_sevdesk_transport(seite=seite)),
                                   url="https://sevdesk.test/api/v1", schlafen=lambda _: None)
    ordner = tmp_path / "sicherung"
    return sevdesk.sichern(client, ordner), ordner


def test_sichern_holt_alles_seitenweise(tmp_path):
    u, ordner = _sichern(tmp_path, seite=2)
    assert u["anzahl"]["Voucher"] == len(VOUCHERS)
    assert u["anzahl"]["Contact"] == len(KONTAKTE)
    assert json.loads((ordner / "Invoice.json").read_text())[0]["invoiceNumber"] == "RE-1001"
    # Belege mit Dokument (10, 11) und Rechnungen ohne Entwurf (20, 21, 22)
    assert set(u["dateien"]) == {"Voucher/10", "Invoice/20", "Invoice/21", "Invoice/22"}
    assert u["ohne_datei"] == ["Voucher/11"]
    assert (ordner / u["dateien"]["Voucher/10"]["datei"]).read_bytes() == PDF
    csv = (ordner / "kontakte.csv").read_text(encoding="utf-8-sig")
    assert "1001;Acme AG;DE222222222;;Weg 1;12345;Musterstadt;buch@acme.de" in csv
    assert ";Erika Muster;" in csv


def test_sichern_laedt_vorhandene_dateien_nicht_neu(tmp_path):
    _sichern(tmp_path)
    anfragen: list = []
    client = sevdesk.SevdeskClient("sd-token", httpx.Client(transport=_sevdesk_transport(anfragen)),
                                   url="https://sevdesk.test/api/v1")
    sevdesk.sichern(client, tmp_path / "sicherung")
    assert not [a for a in anfragen if a.url.path.endswith(("getPdf", "downloadDocument")) and "/10/" in a.url.path]
    assert [a for a in anfragen if a.url.path.endswith("Voucher/11/downloadDocument")]  # hatte keine Datei


def test_falscher_token(tmp_path):
    client = sevdesk.SevdeskClient(
        "x", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))), url="https://s.test")
    try:
        sevdesk.sichern(client, tmp_path / "s")
    except sevdesk.SevdeskFehler as e:
        assert "API-Token" in str(e)
    else:
        raise AssertionError("Fehler erwartet")


def _kandidaten(tmp_path):
    _sichern(tmp_path)
    return {(k.objekt, k.sevdesk_id): k for k in sevdesk.kandidaten(sevdesk.Sicherung.laden(tmp_path / "sicherung"))}


def test_abbildung_der_steuerregeln(tmp_path):
    k = _kandidaten(tmp_path)

    hosting = k[("Voucher", "10")]
    assert [(b.steuersatz, b.netto, b.steuer) for b in hosting.belege] == [
        (D(19), D("100.00"), D("19.00")), (D(7), D("10.00"), D("0.70"))]
    b = hosting.belege[0]
    assert (b.art, b.steuerfall, b.partner, b.partner_ustid) == (
        Art.EINGANG, Steuerfall.VORSTEUER_INLAND, "Hosting GmbH", "DE111111111")
    assert (b.rechnungsdatum, b.zahlungsdatum, b.rechnungsnummer) == (date(2024, 3, 15), date(2024, 3, 20), "H-2024-03")
    assert b.beschreibung == "Server, Buch"
    assert hosting.datei is not None and not b.pruefen and not b.hinweise

    jetbrains = k[("Voucher", "11")].belege[0]
    assert (jetbrains.steuerfall, jetbrains.steuer) == (Steuerfall.VORSTEUER_INLAND, D("19.00"))
    assert k[("Voucher", "11")].datei is None

    openai = k[("Voucher", "12")].belege[0]
    assert (openai.steuerfall, openai.steuersatz, openai.steuer) == (Steuerfall.AUSLAND_13B, D(19), None)
    assert (openai.netto, openai.waehrung) == (D("18.40"), "EUR")
    assert openai.hinweise == ["Original in USD (Kurs 1.0870); Beträge in EUR wie in sevDesk gebucht."]

    assert k[("Voucher", "13")].grund == "Entwurf"
    assert k[("Voucher", "14")].grund.startswith("Vorlage")

    alt = k[("Voucher", "15")].belege[0]  # taxType default, Steuersatz aus den Summen
    assert (alt.steuerfall, alt.steuersatz, alt.rechnungsdatum) == (Steuerfall.VORSTEUER_INLAND, D(19),
                                                                      date(2023, 1, 15))

    re1001 = k[("Invoice", "20")].belege[0]
    assert (re1001.art, re1001.steuerfall, re1001.netto, re1001.steuer) == (
        Art.AUSGANG, Steuerfall.INLAND, D("1000.00"), D("190.00"))
    assert re1001.zahlungsdatum == date(2024, 3, 10)
    assert k[("Invoice", "21")].belege[0].zahlungsdatum is None
    storno = k[("Invoice", "22")].belege[0]
    assert (storno.netto, storno.steuer) == (D("-100.00"), D("-19.00"))
    assert k[("Invoice", "23")].grund == "Entwurf"


def test_unsichere_regeln_bekommen_hinweis():
    regel = sevdesk.STEUERREGELN["13"]
    b = sevdesk._belege(regel, Art.EINGANG, {D(0): (D(100), D(0))}, rechnungsdatum=None)[0]
    assert b.steuerfall == Steuerfall.AUSLAND_13B and "ohne Vorsteuerabzug" in b.hinweise[0]
    b = sevdesk._belege(sevdesk.STEUERREGELN["1"], Art.AUSGANG, {D(0): (D(50), D(0))}, rechnungsdatum=None)[0]
    assert b.steuerfall == Steuerfall.IGNORIEREN and b.hinweise
    b = sevdesk._belege(sevdesk.STEUERREGELN["9"], Art.EINGANG, {D(0): (D(5), D(0))}, rechnungsdatum=None)[0]
    assert b.steuerfall == Steuerfall.OHNE_VORSTEUER


def test_probelauf_schreibt_nichts(tmp_path, db, umrechner):
    _sichern(tmp_path)
    s = sevdesk.Sicherung.laden(tmp_path / "sicherung")
    erg = sevdesk.importieren(db, Config(), s, trocken=True)
    assert erg.neu == 7 and len(erg.belege) == 8
    assert erg.uebersprungen == {"Entwurf": 2, "Vorlage für wiederkehrende Belege": 1}
    assert db.alle_belege() == [] and db.sevdesk_importe() == {}
    assert "Beleg JB-1 JetBrains s.r.o." in erg.ohne_datei


def test_import_ohne_paperless_ist_wiederholbar(tmp_path, db):
    _sichern(tmp_path)
    s = sevdesk.Sicherung.laden(tmp_path / "sicherung")
    erg = sevdesk.importieren(db, Config(), s, paperless_nutzen=False, von="2024-01")
    assert (erg.neu, erg.ausserhalb, erg.fehler) == (6, 1, [])
    belege = db.alle_belege()
    assert len(belege) == 7
    assert {b.quelle_ref for b in belege if b.rechnungsnummer == "H-2024-03"} == {"Voucher/10", "Voucher/10#2"}
    assert all(b.quelle == "sevdesk" and not b.pruefen for b in belege)

    erg = sevdesk.importieren(db, Config(), s, paperless_nutzen=False)
    assert (erg.neu, erg.vorhanden) == (1, 6)  # nur der Beleg von 2023 kommt dazu
    assert len(db.alle_belege()) == 8


def test_schon_vorhandene_belege_werden_nicht_doppelt_angelegt(tmp_path, db):
    db.beleg_anlegen(Beleg(art=Art.AUSGANG, steuerfall=Steuerfall.INLAND, rechnungsdatum=date(2024, 2, 28),
                           netto=D("1000"), rechnungsnummer="re-1001", quelle="paperless", quelle_ref="5"))
    _sichern(tmp_path)
    erg = sevdesk.importieren(db, Config(), sevdesk.Sicherung.laden(tmp_path / "sicherung"), paperless_nutzen=False)
    assert erg.doppelt == ["Rechnung RE-1001 Acme AG (gleiche Rechnungsnummer und Datum schon in finanz-O-mat)"]
    assert sevdesk.importieren(db, Config(), sevdesk.Sicherung.laden(tmp_path / "sicherung"),
                               paperless_nutzen=False).vorhanden == 7


def _paperless_transport(protokoll: dict):
    tasks: dict[str, dict] = {}
    protokoll.update({"hochgeladen": [], "tags": [], "korrespondenten": []})

    def handler(request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        if request.method == "GET" and pfad == "/api/tags/":
            return httpx.Response(200, json={"next": None, "results": [{"id": 1, "name": "Eingangsrechnung"}]})
        if request.method == "GET" and pfad == "/api/correspondents/":
            return httpx.Response(200, json={"next": None, "results": [{"id": 7, "name": "acme ag"}]})
        if request.method == "POST" and pfad in ("/api/tags/", "/api/correspondents/"):
            name = json.loads(request.content)["name"]
            protokoll["tags" if "tags" in pfad else "korrespondenten"].append(name)
            return httpx.Response(201, json={"id": 50 + len(protokoll["tags"]) + len(protokoll["korrespondenten"]),
                                             "name": name})
        if pfad == "/api/documents/post_document/":
            text = request.content.decode("latin-1")
            protokoll["hochgeladen"].append(text)
            nr = len(protokoll["hochgeladen"])
            task = f"task-{nr}"
            if "Rechnung RE-1002" in text:  # gab es in Paperless schon
                tasks[task] = {"status": "FAILURE", "result": "Not consuming RE.pdf: It is a duplicate of RE (#300)."}
            else:
                tasks[task] = {"status": "SUCCESS", "related_document": str(200 + nr)}
            return httpx.Response(200, json=task)
        if pfad == "/api/tasks/":
            task = request.url.params["task_id"]
            if protokoll.get("noch_nicht_fertig") == task:
                protokoll.pop("noch_nicht_fertig")
                return httpx.Response(200, json=[{"task_id": task, "status": "STARTED"}])
            return httpx.Response(200, json=[{"task_id": task, **tasks[task]}])
        raise AssertionError(f"unerwartet: {request.method} {pfad}")

    return httpx.MockTransport(handler)


def test_import_mit_paperless(tmp_path, db):
    _sichern(tmp_path)
    s = sevdesk.Sicherung.laden(tmp_path / "sicherung")
    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    protokoll: dict = {"noch_nicht_fertig": "task-1"}
    erg = sevdesk.importieren(db, cfg, s, client=httpx.Client(transport=_paperless_transport(protokoll)),
                              schlafen=lambda _: None)
    assert erg.fehler == []
    assert erg.hochgeladen == 4  # Belege und Rechnungen mit Datei
    assert sorted(protokoll["tags"]) == ["Ausgangsrechnung", "sevDesk"]
    assert protokoll["korrespondenten"] == ["Hosting GmbH"]  # "Acme AG" gibt es schon (Groß-/Kleinschreibung egal)
    hosting_upload = protokoll["hochgeladen"][0]
    assert 'name="created"' in hosting_upload and "2024-03-15" in hosting_upload

    belege = {(b.rechnungsnummer, b.quelle_ref): b for b in db.alle_belege()}
    hosting = belege[("H-2024-03", "201")]
    assert hosting.quelle == "paperless"
    assert hosting.quelle_link == "http://paperless.local/documents/201/details"
    assert ("H-2024-03", "201#2") in belege
    # In Paperless schon vorhanden, aber noch nicht in finanz-O-mat: Beleg verweist auf das alte Dokument
    assert belege[("RE-1002", "300")].quelle == "paperless"
    # ohne Datei: nur die Buchung
    assert belege[("JB-1", "Voucher/11")].quelle == "sevdesk"

    zweiter = sevdesk.importieren(db, cfg, s, client=httpx.Client(transport=_paperless_transport({})))
    assert (zweiter.neu, zweiter.hochgeladen) == (0, 0)


def test_paperless_noch_nicht_fertig_wird_beim_naechsten_lauf_abgeschlossen(tmp_path, db):
    _sichern(tmp_path)
    s = sevdesk.Sicherung.laden(tmp_path / "sicherung")
    cfg = Config(paperless_url="http://paperless.local", paperless_token="t")
    protokoll: dict = {"noch_nicht_fertig": "task-1"}
    transport = _paperless_transport(protokoll)
    zeit = iter(range(0, 1000, 10))
    erg = sevdesk.importieren(db, cfg, s, client=httpx.Client(transport=transport), warten=0,
                              uhr=lambda: next(zeit), von="2024-03", bis="2024-03")
    assert erg.wartet == 1
    assert db.sevdesk_importe()[("Voucher", "10")]["status"] == "wartet"
    erg = sevdesk.importieren(db, cfg, s, client=httpx.Client(transport=transport), von="2024-03", bis="2024-03")
    assert (erg.neu, erg.hochgeladen) == (1, 0)
    assert db.sevdesk_importe()[("Voucher", "10")]["paperless_id"] == "201"


def test_erwartet_lesen(tmp_path):
    datei = tmp_path / "ustva.csv"
    datei.write_text("Zeitraum;Zahllast\n2024-01;1.234,56\n02/2024;-12,30\n03.2024;\"99.10 €\"\nSumme;5\n",
                     encoding="utf-8")
    assert sevdesk.erwartet_lesen(datei) == {"2024-01": D("1234.56"), "2024-02": D("-12.30"), "2024-03": D("99.10")}


def test_monatsvergleich(tmp_path, db):
    _sichern(tmp_path)
    erg = sevdesk.importieren(db, Config(), sevdesk.Sicherung.laden(tmp_path / "sicherung"), trocken=True)
    umrechner = Umrechner(db, ezb_abrufen=False, heute=date(2026, 10, 10))
    monate = sevdesk.monate(erg.belege, "ist", von="2024-01")
    assert monate == ["2024-03"]
    # März 2024 (Ist): USt 1000 € - Storno 100 € = 900 € -> 171,00; Vorsteuer 19,00 + 0,70 + 19,00;
    # OpenAI 18,40 € Reverse Charge: 3,50 USt und 3,50 Vorsteuer
    zeilen = sevdesk.monatsvergleich(erg.belege, umrechner, "ist", monate, {"2024-03": D("132.30")})
    z = zeilen[0]
    assert z["zahllast"] == D("132.30") and z["differenz"] == D("0") and z["passt"]
    zeilen = sevdesk.monatsvergleich(erg.belege, umrechner, "ist", monate, {"2024-03": D("140")})
    assert not zeilen[0]["passt"]
