"""Verlauf des Kontostands für den Reiter „Konten“.

Gemerkt wird der Kontostand erst seit dieser Funktion (Tabelle ``konto_salden``, einmal je Tag und Abruf). Für die
Zeit davor rechnet der Verlauf vom aktuellen Saldo über die gebuchten Umsätze zurück: Stand am Ende des Vortags =
Stand am Ende des Tages minus die Umsätze des Tages. Liegt für einen Tag ein gemerkter Stand vor, gilt der, so
gleichen sich Lücken in den Umsätzen (etwa vor dem ersten Kontoauszug) nicht über die ganze Zeit fort.
Vorgemerkte Umsätze zählen nicht mit, der Saldo der Bank ist der gebuchte.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

Verlauf = list[tuple[date, Decimal]]


def konto(saldo: Decimal | None, stand: date | None, buchungen: Iterable[tuple[date, Decimal]],
          salden: dict[date, Decimal], heute: date) -> Verlauf:
    """Stand am Ende jedes Tages vom Vortag der ersten Buchung (Anfangsbestand) bis heute, älteste zuerst."""
    if saldo is None or stand is None:
        if not salden:
            return []
        stand = max(salden)
        saldo = salden[stand]
    je_tag: dict[date, Decimal] = {}
    for tag, betrag in buchungen:
        je_tag[tag] = je_tag.get(tag, Decimal(0)) + betrag
    anfang = min([*je_tag, *salden, stand])
    if je_tag and anfang == min(je_tag):
        anfang -= timedelta(days=1)  # Anfangsbestand vor der ersten Buchung

    werte: dict[date, Decimal] = {}
    wert = saldo
    tag = stand
    while tag >= anfang:
        if tag != stand and tag in salden:
            wert = salden[tag]
        werte[tag] = wert
        wert -= je_tag.get(tag, Decimal(0))
        tag -= timedelta(days=1)
    # Nach dem Stichtag der Bank (z. B. Abschluss vom Vortag) mit den Buchungen danach weiter
    wert = saldo
    tag = stand + timedelta(days=1)
    while tag <= heute:
        wert = salden.get(tag, wert + je_tag.get(tag, Decimal(0)))
        werte[tag] = wert
        tag += timedelta(days=1)
    return sorted(werte.items())


def summe(verlaeufe: Iterable[Verlauf]) -> Verlauf:
    """Summe mehrerer Verläufe je Tag. Vor seinem ersten Tag zählt ein Konto mit seinem ersten Stand, nach dem
    letzten mit dem letzten, damit die Summe nicht springt, wenn die Umsätze eines Kontos später beginnen."""
    liste = [dict(v) for v in verlaeufe if v]
    if not liste:
        return []
    anfang = min(min(v) for v in liste)
    ende = max(max(v) for v in liste)
    ergebnis: Verlauf = []
    letzte = [v[min(v)] for v in liste]
    tag = anfang
    while tag <= ende:
        for i, v in enumerate(liste):
            if tag in v:
                letzte[i] = v[tag]
        ergebnis.append((tag, sum(letzte, Decimal(0))))
        tag += timedelta(days=1)
    return ergebnis

