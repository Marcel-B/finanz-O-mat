from datetime import date

from ustva.fristen import abgabefrist, feiertage, naechste_frist, ostersonntag


def test_fristen_aus_projektstand():
    assert abgabefrist(2026, 9) == date(2026, 10, 12)  # 10.10. ist Samstag
    assert abgabefrist(2026, 10) == date(2026, 11, 10)
    assert abgabefrist(2026, 11) == date(2026, 12, 10)


def test_feiertag_verschiebt_frist():
    # 10.04.2023 war Ostermontag -> Dienstag 11.04.
    assert abgabefrist(2023, 3) == date(2023, 4, 11)


def test_landesfeiertag():
    # Fronleichnam 2020 war am 11.06.; 10.06.2020 Mittwoch, also keine Verschiebung
    assert date(2020, 6, 11) in feiertage(2020, "NW")
    assert date(2020, 6, 11) not in feiertage(2020, "BE")


def test_dauerfristverlaengerung():
    assert abgabefrist(2026, 9, dauerfrist=True) == date(2026, 11, 10)


def test_ostern():
    assert ostersonntag(2026) == date(2026, 4, 5)
    assert ostersonntag(2025) == date(2025, 4, 20)


def test_naechste_frist():
    assert naechste_frist(date(2026, 10, 10)).zeitraum == "2026-09"
    assert naechste_frist(date(2026, 10, 12)).zeitraum == "2026-09"
    assert naechste_frist(date(2026, 10, 13)).zeitraum == "2026-10"
    f = naechste_frist(date(2026, 10, 1))
    assert f.zeitraum == "2026-09" and f.tage_bis == 11


def test_naechste_frist_mit_dauerfrist():
    assert naechste_frist(date(2026, 10, 10), dauerfrist=True).zeitraum == "2026-08"


def test_naechste_frist_ueberspringt_abgegebene_monate():
    gefragt = []

    def abgegeben(jahr, monat):
        gefragt.append((jahr, monat))
        return (jahr, monat) == (2026, 9)

    f = naechste_frist(date(2026, 10, 10), abgegeben=abgegeben)
    assert f.zeitraum == "2026-10" and f.faellig == date(2026, 11, 10)
    assert f.to_dict()["abgegeben"] == ["September 2026"]
    assert gefragt == [(2026, 9), (2026, 10)]  # abgelaufene Fristen werden gar nicht erst gefragt
    assert naechste_frist(date(2026, 10, 10)).to_dict()["abgegeben"] == []
