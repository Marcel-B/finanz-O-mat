"""Belege aus Paperless-ngx über die REST-API einlesen.

Ob ein Dokument eine Ausgangs- oder Eingangsrechnung ist, entscheidet ein Tag
oder Dokumenttyp (Standard: "Ausgangsrechnung" / "Eingangsrechnung"). Beträge
kommen bevorzugt aus benutzerdefinierten Feldern (z. B. ein Geldfeld "Netto"),
sonst aus dem OCR-Text des Dokuments. Ist das Originaldokument eine E-Rechnung
(ZUGFeRD/Factur-X), gelten die Angaben aus der eingebetteten XML.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from .. import erechnung
from ..config import Config
from ..db import Datenbank
from ..extraktion import betrag_parsen, erkennen, steuerfall_vorschlagen
from ..fristen import monat_plus
from ..models import Beleg
from ..steuerfaelle import Art
from . import ImportErgebnis, beleg_aus_erkennung, speichern

log = logging.getLogger(__name__)

RE_GELDFELD = re.compile(r"^([A-Z]{3})?\s?(-?[\d.,]+)$")


class PaperlessFehler(Exception):
    pass


def geldfeld_parsen(wert) -> tuple[Decimal, str | None] | None:
    """Paperless speichert Geldfelder als z. B. "EUR123.45" oder "123.45"."""
    if wert in (None, ""):
        return None
    if isinstance(wert, (int, float)):
        return Decimal(str(wert)), None
    m = RE_GELDFELD.match(str(wert).strip())
    if not m:
        return None
    betrag = betrag_parsen(m.group(2))
    return (betrag, m.group(1)) if betrag is not None else None


def _datum(wert: str | None) -> date | None:
    if not wert:
        return None
    try:
        return date.fromisoformat(str(wert)[:10])
    except ValueError:
        return None


def _fehlertext(antwort: httpx.Response) -> str:
    try:
        detail = antwort.json().get("detail", "")
    except (ValueError, AttributeError):
        detail = ""
    return f"Paperless antwortet mit Fehler {antwort.status_code}" + (f": {detail}" if detail else "")


class PaperlessClient:
    """Zugriff auf die REST-API.

    Paperless versioniert die API über den Accept-Header und lehnt ältere
    Versionen mit 406 ab (aktuell nur 9 und 10). Deshalb fragt der Client ohne
    Version an und übernimmt für alle weiteren Anfragen die Version, die der
    Server im Kopf ``X-Api-Version`` meldet. Die ausgewerteten Felder sind in
    allen Versionen gleich.

    Leitet Paperless um (etwa von http auf https), folgt der Client beim Lesen
    und nimmt die neue Adresse für alle weiteren Anfragen. Schreibende Anfragen
    folgen keiner Umleitung, weil httpx aus einem umgeleiteten POST ein GET macht.
    """

    def __init__(self, url: str, token: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        self.client.follow_redirects = True
        self.client.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})
        self.api_version: str | None = None

    def _get(self, adresse: str, params: dict | None = None) -> httpx.Response:
        antwort = self.client.get(adresse, params=params)
        if antwort.history and adresse.startswith(self.url):
            pfad = adresse[len(self.url):]
            ziel = str(antwort.url.copy_with(query=None))
            if ziel.endswith(pfad) and ziel[:-len(pfad)] != self.url:
                log.warning("Paperless leitet von %s auf %s um; bitte die Adresse in den Einstellungen anpassen",
                            self.url, ziel[:-len(pfad)])
                self.url = ziel[:-len(pfad)]
        version = antwort.headers.get("x-api-version", "")
        if self.api_version is None and version.isdigit():
            self.api_version = version
            self.client.headers["Accept"] = f"application/json; version={version}"
        return antwort

    def _alle(self, pfad: str, params: dict | None = None) -> list[dict]:
        ergebnisse: list[dict] = []
        naechste: str | None = f"{self.url}{pfad}"
        params = {"page_size": 100, **(params or {})}
        while naechste:
            try:
                antwort = self._get(naechste, params=params)
                antwort.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise PaperlessFehler("Paperless lehnt den Zugriff ab, bitte Token prüfen") from e
                raise PaperlessFehler(_fehlertext(e.response)) from e
            except httpx.HTTPError as e:
                raise PaperlessFehler(f"Paperless nicht erreichbar: {e}") from e
            daten = antwort.json()
            ergebnisse.extend(daten.get("results", []))
            naechste = daten.get("next")
            params = None  # "next" enthält die Parameter bereits
        return ergebnisse

    def senden(self, methode: str, pfad: str, daten: dict) -> dict:
        """Schreibender Aufruf (PATCH/POST) mit JSON; Fehler als PaperlessFehler."""
        try:
            antwort = self.client.request(methode, f"{self.url}{pfad}", json=daten, follow_redirects=False)
            antwort.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise PaperlessFehler("Paperless lehnt die Änderung ab, bitte Token und Rechte prüfen") from e
            raise PaperlessFehler(_fehlertext(e.response)) from e
        except httpx.HTTPError as e:
            raise PaperlessFehler(f"Paperless nicht erreichbar: {e}") from e
        try:
            return antwort.json()
        except ValueError:
            return {}

    def alle_dokumente(self, params: dict | None = None) -> list[dict]:
        return self._alle("/api/documents/", params)

    def namen(self, pfad: str) -> dict[int, str]:
        return {e["id"]: e["name"] for e in self._alle(pfad)}

    def felder(self) -> dict[int, str]:
        try:
            return self.namen("/api/custom_fields/")
        except PaperlessFehler:
            return {}  # ältere Paperless-Versionen ohne benutzerdefinierte Felder

    def original(self, dok_id: int) -> bytes:
        """Originaldatei; die archivierte PDF/A von Paperless enthält keine eingebettete XML mehr."""
        antwort = self._get(f"{self.url}/api/documents/{dok_id}/download/", params={"original": "true"})
        antwort.raise_for_status()
        return antwort.content

    def herunterladen(self, dok_id: int) -> httpx.Response:
        """Archivierte Fassung (meist PDF/A), sonst das Original."""
        try:
            antwort = self._get(f"{self.url}/api/documents/{dok_id}/download/")
            antwort.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise PaperlessFehler(f"Dokument {dok_id} gibt es in Paperless nicht mehr") from e
            raise PaperlessFehler(_fehlertext(e.response)) from e
        except httpx.HTTPError as e:
            raise PaperlessFehler(f"Paperless nicht erreichbar: {e}") from e
        return antwort

    def dokument(self, dok_id: int) -> dict:
        try:
            antwort = self._get(f"{self.url}/api/documents/{dok_id}/")
            antwort.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise PaperlessFehler(f"Dokument {dok_id} gibt es in Paperless nicht mehr") from e
            raise PaperlessFehler(_fehlertext(e.response)) from e
        except httpx.HTTPError as e:
            raise PaperlessFehler(f"Paperless nicht erreichbar: {e}") from e
        return antwort.json()

    def dokumente(self, von: date, bis_exklusiv: date, tag_id: int | None = None) -> list[dict]:
        params = {"created__gte": von.isoformat(), "created__lt": bis_exklusiv.isoformat(), "ordering": "created"}
        if tag_id is not None:
            params["tags__id__all"] = str(tag_id)
        return self._alle("/api/documents/", params)


def importieren(
    db: Datenbank, cfg: Config, jahr: int, monat: int, client: httpx.Client | None = None
) -> ImportErgebnis:
    """Liest die Rechnungen eines Monats ein (nach Ausstellungsdatum in Paperless).

    Die zwei Vormonate werden mit abgefragt, damit bei Ist-Versteuerung auch
    ältere Rechnungen ankommen, die erst in diesem Monat bezahlt wurden. Bereits
    eingelesene Dokumente werden übersprungen.
    """
    ergebnis = ImportErgebnis()
    if not cfg.paperless_aktiv:
        ergebnis.fehler.append("Paperless ist nicht konfiguriert (PAPERLESS_URL und PAPERLESS_TOKEN)")
        return ergebnis
    pl = PaperlessClient(cfg.paperless_url, cfg.paperless_token, client)
    try:
        tags = pl.namen("/api/tags/")
        typen = pl.namen("/api/document_types/")
        korrespondenten = pl.namen("/api/correspondents/")
        felder = pl.felder()
        vj, vm = monat_plus(jahr, monat, -2)
        nj, nm = monat_plus(jahr, monat, 1)
        dokumente = pl.dokumente(date(vj, vm, 1), date(nj, nm, 1))
    except PaperlessFehler as e:
        ergebnis.fehler.append(str(e))
        return ergebnis

    ausgang = {n.lower() for n in cfg.paperless_tags_ausgang}
    eingang = {n.lower() for n in cfg.paperless_tags_eingang}
    vorhandene = db.alle_belege()

    for dok in dokumente:
        namen = {tags.get(t, "").lower() for t in dok.get("tags", [])}
        if dok.get("document_type") is not None:
            namen.add(typen.get(dok["document_type"], "").lower())
        if namen & ausgang:
            art = Art.AUSGANG
        elif namen & eingang:
            art = Art.EINGANG
        else:
            ergebnis.uebersprungen += 1
            continue
        ref = str(dok["id"])
        if db.beleg_vorhanden("paperless", ref):
            ergebnis.vorhanden += 1
            continue
        try:
            speichern(db, dokument_belege(pl, dok, art, cfg, felder, korrespondenten), vorhandene, ergebnis,
                      f"Dokument {dok['id']} ({dok.get('title')})")
        except Exception as e:  # ein fehlerhaftes Dokument soll den Import nicht abbrechen
            ergebnis.fehler.append(f"Dokument {dok.get('id')} ({dok.get('title')}): {e}")
    return ergebnis


def dokument_belege(
    pl: PaperlessClient, dok: dict, art: Art, cfg: Config, felder: dict[int, str], korrespondenten: dict[int, str]
) -> list:
    """Alle Belege eines Paperless-Dokuments, fertig zum Speichern."""
    ref = str(dok["id"])
    werte = {felder.get(f["field"], "").lower(): f.get("value") for f in dok.get("custom_fields", [])}
    gemeinsam = {
        "quelle": "paperless",
        "quelle_link": f"{cfg.paperless_url}/documents/{dok['id']}/details",
        "beschreibung": dok.get("title") or "",
    }
    zahlung = _datum(werte.get(cfg.paperless_feld_zahlungsdatum.lower()))
    if e_belege := _erechnung(pl, dok, art, cfg):
        for i, beleg in enumerate(e_belege):
            for name, wert in gemeinsam.items():
                setattr(beleg, name, wert)
            beleg.quelle_ref = ref if i == 0 else f"{ref}#{i + 1}"
            if korrespondent := korrespondenten.get(dok.get("correspondent")):
                beleg.partner = korrespondent
            beleg.zahlungsdatum = zahlung
            beleg.rechnungsdatum = beleg.rechnungsdatum or _datum(dok.get("created"))
        return e_belege
    return [text_beleg(dok, art, cfg, felder, korrespondenten)]


def text_beleg(
    dok: dict, art: Art, cfg: Config, felder: dict[int, str], korrespondenten: dict[int, str]
) -> Beleg:
    """Beleg aus OCR-Text und benutzerdefinierten Feldern, ohne die Originaldatei zu laden."""
    werte = {felder.get(f["field"], "").lower(): f.get("value") for f in dok.get("custom_fields", [])}
    erkennung = erkennen(dok.get("content") or "", art, cfg.eigene_ustid)
    _felder_uebernehmen(erkennung, werte, cfg)
    beleg = beleg_aus_erkennung(
        erkennung, art, quelle="paperless", quelle_ref=str(dok["id"]),
        quelle_link=f"{cfg.paperless_url}/documents/{dok['id']}/details", beschreibung=dok.get("title") or "",
        partner=korrespondenten.get(dok.get("correspondent"), "") if dok.get("correspondent") else "",
    )
    if zahlung := _datum(werte.get(cfg.paperless_feld_zahlungsdatum.lower())):
        beleg.zahlungsdatum = zahlung
    if beleg.rechnungsdatum is None:
        beleg.rechnungsdatum = _datum(dok.get("created"))
    return beleg


def _erechnung(pl: PaperlessClient, dok: dict, art: Art, cfg: Config) -> list | None:
    """Belege aus einer ZUGFeRD-/Factur-X-Datei, sonst None (dann gilt der OCR-Text)."""
    name = (dok.get("original_file_name") or "").lower()
    mime = dok.get("mime_type") or ""
    if not (name.endswith((".pdf", ".xml")) or mime in ("application/pdf", "application/xml", "text/xml")):
        return None
    try:
        e = erechnung.lesen(pl.original(dok["id"]), name)
    except (httpx.HTTPError, erechnung.ERechnungFehler) as fehler:
        log.warning("Paperless-Dokument %s: E-Rechnung nicht lesbar: %s", dok.get("id"), fehler)
        return None
    return erechnung.belege(e, art, cfg.eigene_ustid) if e else None


def _felder_uebernehmen(e, werte: dict, cfg: Config) -> None:
    """Benutzerdefinierte Felder haben Vorrang vor dem OCR-Text."""
    netto = geldfeld_parsen(werte.get(cfg.paperless_feld_netto.lower()))
    brutto = geldfeld_parsen(werte.get(cfg.paperless_feld_brutto.lower()))
    steuer = geldfeld_parsen(werte.get(cfg.paperless_feld_steuer.lower()))
    satz = werte.get(cfg.paperless_feld_steuersatz.lower())
    if satz not in (None, ""):
        try:
            e.steuersatz = Decimal(str(satz).replace("%", "").replace(",", ".").strip())
        except InvalidOperation:
            pass
    for feld in (netto, brutto, steuer):
        if feld and feld[1]:
            e.waehrung = feld[1]
    if steuer:
        e.steuer = steuer[0]
    if netto:
        e.netto = netto[0]
        if brutto and not steuer:
            e.steuer = brutto[0] - netto[0]
    elif brutto:
        e.brutto = brutto[0]
        if e.steuer is not None:
            e.netto = brutto[0] - e.steuer
        elif e.steuersatz is not None:
            e.netto = (brutto[0] * 100 / (100 + e.steuersatz)).quantize(Decimal("0.01"))
        else:
            e.netto = brutto[0]
    if netto or brutto:
        e.hinweise = [h for h in e.hinweise if not h.startswith("Kein Betrag")]
        e.steuerfall = steuerfall_vorschlagen(e)
