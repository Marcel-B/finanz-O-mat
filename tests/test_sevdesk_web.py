import io
import time
import zipfile
from datetime import date

from fastapi.testclient import TestClient

from ustva.config import Config
from ustva.sevdesk_web import speichern
from ustva.web import create_app

from .test_sevdesk_export import _export


def test_speichern_legt_nur_brauchbare_dateien_ab(tmp_path):
    ziel = tmp_path / "ziel"
    assert speichern(ziel, "sevDesk/voucher.csv", b"a") == 1
    assert speichern(ziel, "sevDesk/Belege_11/2024-03-15_Ausgabebeleg_X_abc.pdf", b"%PDF") == 1
    assert speichern(ziel, "sevDesk/.DS_Store", b"x") == 0
    assert speichern(ziel, "sevDesk/notizen.docx", b"x") == 0
    assert speichern(ziel, "../../etc/passwd.pdf", b"x") == 1  # nur der Dateiname zählt
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("Rechnungen_11/2024-02-03_Rechnung_RE-1000_abc.pdf", b"%PDF")
        z.writestr("__MACOSX/._x.pdf", b"x")
        z.writestr("Rechnungen_11/", b"")
    assert speichern(ziel, "Rechnungen.zip", puffer.getvalue()) == 1
    assert sorted(p.name for p in (ziel / "dateien").iterdir()) == [
        "2024-02-03_Rechnung_RE-1000_abc.pdf", "2024-03-15_Ausgabebeleg_X_abc.pdf", "passwd.pdf",
    ]
    assert (ziel / "voucher.csv").read_bytes() == b"a"
    assert not (tmp_path / "etc").exists()


def _warten(c: TestClient) -> dict:
    for _ in range(200):
        s = c.get("/api/sevdesk").json()
        if not s["laeuft"]:
            return s
        time.sleep(0.05)
    raise AssertionError("Übernahme wird nicht fertig")


def test_hochladen_probelauf_uebernehmen(tmp_path, db, umrechner):
    quelle = _export(tmp_path)
    c = TestClient(create_app(Config(), db, umrechner, heute=date(2026, 10, 10), sevdesk_ordner=tmp_path / "archiv"))
    assert c.get("/api/sevdesk").json()["csv"] == []
    assert c.post("/api/sevdesk/probelauf", json={}).status_code == 400

    for datei in sorted(p for p in quelle.rglob("*") if p.is_file()):
        name = str(datei.relative_to(tmp_path))
        assert c.put("/api/sevdesk/dateien", params={"name": name}, content=datei.read_bytes()).status_code == 200
    status = c.get("/api/sevdesk").json()
    assert (status["csv"], status["dateien"], status["paperless"]) == \
        (["contacts.csv", "invoices.csv", "voucher.csv"], 5, False)

    probe = c.post("/api/sevdesk/probelauf", json={}).json()
    assert (probe["trocken"], probe["neu"], probe["belege"]) == (True, 9, 10)
    assert db.alle_belege() == []
    assert any(z["zeitraum"] == "2024-03" for z in probe["vergleich"])
    assert c.post("/api/sevdesk/probelauf", json={"bis": "2024-02"}).json()["neu"] == 3  # Erlös, RE-1000, STRATO 2016

    assert c.post("/api/sevdesk/importieren", json={}).status_code == 202
    fertig = _warten(c)
    assert fertig["fehler"] == ""
    assert (fertig["ergebnis"]["trocken"], fertig["ergebnis"]["neu"]) == (False, 9)
    assert len(db.alle_belege()) == 10
    assert {k.name for k in db.kontakte()} >= {"Acme GmbH", "Cursor"}

    # zweiter Lauf legt nichts doppelt an
    c.post("/api/sevdesk/importieren", json={})
    assert _warten(c)["ergebnis"]["vorhanden"] == 9
    assert len(db.alle_belege()) == 10

    assert c.delete("/api/sevdesk/dateien").status_code == 204
    assert c.get("/api/sevdesk").json()["dateien"] == 0
