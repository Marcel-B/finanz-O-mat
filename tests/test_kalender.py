from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from ustva import kalender
from ustva.config import Config
from ustva.web import create_app

ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Test//DE\r
BEGIN:VEVENT\r
UID:1\r
SUMMARY:Urlaub Ostsee\r
DTSTART;VALUE=DATE:20261019\r
DTEND;VALUE=DATE:20261022\r
BEGIN:VALARM\r
TRIGGER:-P1D\r
SUMMARY:Erinnerung\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:2\r
SUMMARY:Besprechung mit langem Titel, der umbrochen\r
  wird\r
DTSTART;TZID=Europe/Berlin:20261015T100000\r
DTEND;TZID=Europe/Berlin:20261015T110000\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:3\r
SUMMARY:Geburtstag Erika\r
DTSTART;VALUE=DATE:20200314\r
RRULE:FREQ=YEARLY\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:4\r
SUMMARY:Brückentag\r
DTSTART;VALUE=DATE:20261030\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:5\r
SUMMARY:Abgesagt\r
STATUS:CANCELLED\r
DTSTART;VALUE=DATE:20261027\r
DTEND;VALUE=DATE:20261028\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:6\r
SUMMARY:Krank\\, Erkältung\r
DTSTART;VALUE=DATE:20261012\r
DURATION:P2D\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_nur_ganztaegige_termine():
    termine = kalender.termine(ICS)
    assert [(t.titel, t.beginn, t.ende) for t in termine] == [
        ("Urlaub Ostsee", date(2026, 10, 19), date(2026, 10, 22)),
        ("Brückentag", date(2026, 10, 30), date(2026, 10, 31)),
        ("Krank, Erkältung", date(2026, 10, 12), date(2026, 10, 14)),
    ]


def test_freie_tage_mit_stichwort():
    termine = kalender.termine(ICS)
    alle = kalender.freie_tage(termine)
    assert sorted(alle) == [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 19), date(2026, 10, 20),
                            date(2026, 10, 21), date(2026, 10, 30)]
    assert alle[date(2026, 10, 20)] == "Urlaub Ostsee"
    nur = kalender.freie_tage(termine, "urlaub, KRANK")
    assert sorted(nur) == [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 19), date(2026, 10, 20),
                           date(2026, 10, 21)]


@pytest.mark.parametrize("eingabe, erwartet", [
    ("https://cloud.example.de/apps/calendar/p/AbC123",
     "https://cloud.example.de/remote.php/dav/public-calendars/AbC123?export"),
    ("https://cloud.example.de/index.php/apps/calendar/p/AbC123/dayGridMonth/now",
     "https://cloud.example.de/remote.php/dav/public-calendars/AbC123?export"),
    ("webcal://cloud.example.de/remote.php/dav/public-calendars/AbC123",
     "https://cloud.example.de/remote.php/dav/public-calendars/AbC123?export"),
    ("https://cloud.example.de/remote.php/dav/public-calendars/AbC123?export",
     "https://cloud.example.de/remote.php/dav/public-calendars/AbC123?export"),
    ("https://kalender.example.org/feiertage.ics", "https://kalender.example.org/feiertage.ics"),
])
def test_export_adresse(eingabe, erwartet):
    assert kalender.export_adresse(eingabe) == erwartet


class Zaehler:
    def __init__(self, status=200, text=ICS):
        self.aufrufe, self.status, self.text = [], status, text

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.aufrufe.append(str(request.url))
        return httpx.Response(self.status, text=self.text)


def quelle(zaehler, uhr=lambda: 0.0):
    return kalender.Kalender(lambda: httpx.Client(transport=httpx.MockTransport(zaehler)), uhr)


def test_kalender_merkt_sich_den_stand_und_behaelt_ihn_bei_fehlern():
    jetzt = [0.0]
    z = Zaehler()
    k = quelle(z, lambda: jetzt[0])
    url = "https://cloud.example.de/apps/calendar/p/AbC123"
    tage, fehler = k.tage(url)
    assert len(tage) == 6 and fehler == ""
    assert z.aufrufe == ["https://cloud.example.de/remote.php/dav/public-calendars/AbC123?export"]
    k.tage(url)
    assert len(z.aufrufe) == 1
    jetzt[0] = kalender.CACHE_SEKUNDEN + 1
    z.status = 503
    tage, fehler = k.tage(url)
    assert len(tage) == 6 and "503" in fehler
    k.tage(url)  # nach einem Fehler nicht bei jedem Aufruf erneut
    assert len(z.aufrufe) == 2
    assert k.tage("") == ({}, "")


def test_kein_kalender_unter_der_adresse():
    with pytest.raises(kalender.KalenderFehler, match="kein Kalender"):
        quelle(Zaehler(text="<html>Anmelden</html>")).laden("https://cloud.example.de/kalender")
    with pytest.raises(kalender.KalenderFehler, match="Anmeldung"):
        quelle(Zaehler(status=401)).laden("https://cloud.example.de/kalender")


@pytest.fixture
def client(db, umrechner):
    cfg = Config(bundesland="NW")
    return TestClient(create_app(cfg, db, umrechner, heute=date(2026, 10, 23), urlaubskalender=quelle(Zaehler())))


def test_taetigkeiten_ohne_urlaub_und_feiertage(client):
    r = client.put("/api/einstellungen", json={"kalender_url": "https://cloud.example.de/apps/calendar/p/AbC123",
                                               "kalender_stichwort": "Urlaub, Krank"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["kalender_url"] == {"gesetzt": True, "quelle": "app", "anzeige": "cloud.example.de"}
    assert e["kalender_stichwort"]["wert"] == "Urlaub, Krank"
    assert "AbC123" not in r.text

    for tag in ("2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"):
        assert client.post("/api/taetigkeiten", json={"datum": tag, "stunden": "8"}).status_code == 201
    m = client.get("/api/taetigkeiten?zeitraum=2026-10").json()
    # 3.10. Feiertag (Samstag, zählt nicht), 12./13. krank, 19.–21. Urlaub; heute ist der 23.10.
    assert m["fehlende_tage"] == ["2026-10-09", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-22",
                                  "2026-10-23"]
    assert m["freie_tage"] == [
        {"datum": "2026-10-12", "name": "Krank, Erkältung", "art": "urlaub"},
        {"datum": "2026-10-13", "name": "Krank, Erkältung", "art": "urlaub"},
        {"datum": "2026-10-19", "name": "Urlaub Ostsee", "art": "urlaub"},
        {"datum": "2026-10-20", "name": "Urlaub Ostsee", "art": "urlaub"},
        {"datum": "2026-10-21", "name": "Urlaub Ostsee", "art": "urlaub"},
    ]
    assert m["kalender"] == {"verknuepft": True, "fehler": ""}
    assert m["vorschlag"]["datum"] == "2026-10-09"

    m = client.get("/api/taetigkeiten?zeitraum=2026-12").json()
    assert m["freie_tage"] == [{"datum": "2026-12-25", "name": "1. Weihnachtsfeiertag", "art": "feiertag"}]


def test_kalender_testen(client):
    r = client.post("/api/einstellungen/kalender-test",
                    json={"kalender_url": "webcal://cloud.example.de/remote.php/dav/public-calendars/AbC123",
                          "kalender_stichwort": ""})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == ("Kalender gelesen, 3 ganztägige Termine. Nächster freier Werktag: 30.10.2026 "
                                "(Brückentag), 1 insgesamt ab heute.")
    assert client.post("/api/einstellungen/kalender-test", json={}).status_code == 400
    assert client.put("/api/einstellungen", json={"kalender_url": "cloud.example.de"}).status_code == 400
