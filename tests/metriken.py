"""Prüft, dass /metrics (ohne Anmeldung erreichbar) keine Beträge, IBANs oder Namen zeigt."""

from prometheus_client import CollectorRegistry, generate_latest

from ustva import monitoring


def offen(db) -> str:
    """Text von /metrics zum Suchen nach Geheimem.

    Die Bestandswerte der App kommen ganz; bei den übrigen Reihen (Prozess, Python, Zähler aus anderen Tests) nur
    Namen und Labels, denn ihre Messwerte (Speicher, Laufzeiten) enthalten zufällig jede Ziffernfolge.
    """
    bestand = monitoring.Bestand(db, None, None)
    gesamt = monitoring.metriken(bestand)[0].decode()
    eigene = CollectorRegistry(auto_describe=False)
    eigene.register(bestand)
    nur_bestand = generate_latest(eigene).decode()
    ohne_werte = [z if z.startswith("#") else z.rsplit(" ", 1)[0] for z in gesamt.splitlines()]
    return nur_bestand + "\n".join(ohne_werte)
