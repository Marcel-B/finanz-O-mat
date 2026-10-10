# Finanz-O-Mat

Kleine Webanwendung für die monatliche Umsatzsteuer-Voranmeldung (UStVA). Sie liest Rechnungen aus einem
Verzeichnis oder aus Paperless-ngx ein, berechnet die Kennzahlen für das Formular USt 1 A und zeigt die nächste
Abgabefrist. Die Werte tippst du anschließend selbst in [Mein ELSTER](https://www.elster.de) ab.

Die App übermittelt nichts ans Finanzamt und ist keine Steuerberatung. Bei Sonderfällen beim Finanzamt oder
Steuerberater nachfragen.

## Was die App kann

- Ausgangs- und Eingangsrechnungen aus einem Verzeichnis (PDF mit Textebene, .txt, .xml) oder aus Paperless-ngx
  einlesen. E-Rechnungen (ZUGFeRD 2.x / Factur-X, XRechnung in CII oder UBL) werden exakt aus den strukturierten
  Daten gelesen. Bei anderen Rechnungen werden Beträge, Datum, Steuersatz, Währung, USt-IdNr. und Steuerfall aus dem
  Text geschätzt und müssen einmal bestätigt werden.
- Belege manuell anlegen und korrigieren.
- Kennzahlen berechnen, auch mit Auslandsbezug:

  | Kz | Inhalt | Steuerfall in der App |
  |---|---|---|
  | 81 | Umsätze zu 19 % (netto, volle Euro) | Ausgang: Inland, steuerpflichtig |
  | 86 | Umsätze zu 7 % | Ausgang: Inland, steuerpflichtig |
  | 21 | Sonstige Leistungen an Unternehmer im EU-Ausland | Ausgang: Leistung an EU-Unternehmer |
  | 41 | Innergemeinschaftliche Lieferungen | Ausgang: Innergemeinschaftliche Lieferung |
  | 45 | Übrige nicht steuerbare Umsätze (z. B. Kunde im Drittland) | Ausgang: Leistung an Kunden im Drittland |
  | 43 | Steuerfreie Ausfuhrlieferungen | Ausgang: Ausfuhrlieferung |
  | 66 | Vorsteuer aus Rechnungen mit deutscher USt | Eingang: Rechnung mit deutscher Umsatzsteuer |
  | 46 / 47 | Leistungen von EU-Unternehmern (Reverse Charge, § 13b Abs. 1) | Eingang: Leistung eines EU-Unternehmers |
  | 84 / 85 | Leistungen von Drittland-Unternehmern, z. B. OpenAI (§ 13b Abs. 2) | Eingang: Leistung eines Drittland-Unternehmers |
  | 89 / 93 | Innergemeinschaftlicher Erwerb von Waren | Eingang: Innergemeinschaftlicher Erwerb |
  | 61 / 67 | Vorsteuer aus Erwerb bzw. Reverse Charge | ergibt sich aus 89/93 bzw. 47/85 |
  | 83 | Zahllast bzw. Überschuss | berechnet |

  Kennzahl 500 (ergänzende Angaben, seit 2026 statt Kz 23) bleibt im Normalfall leer.
- Fremdwährungen umrechnen: Maßgeblich ist der monatliche Umsatzsteuer-Umrechnungskurs des BMF (§ 16 Abs. 6 UStG).
  Den trägst du unter „Umrechnungskurse“ ein; ohne Eintrag nimmt die App das Monatsmittel der EZB-Referenzkurse und
  weist darauf hin. Ein Kurs direkt am Beleg hat Vorrang.
- Angaben für die Zusammenfassende Meldung (ZM) an das BZSt auflisten, wenn Kz 21 oder 41 vorkommen.
- Nächste Frist anzeigen: 10. des Folgemonats, bei Wochenende oder Feiertag der nächste Werktag
  (Landesfeiertage über `BUNDESLAND`), optional mit Dauerfristverlängerung.
- Kontoumsätze aus dem N26-CSV-Export einlesen, automatisch den Rechnungen zuordnen und vor Umsätzen ohne Beleg
  warnen. Bei Ausgangsrechnungen übernimmt die Zuordnung das Zahlungsdatum (wichtig für die Ist-Versteuerung).
- CSV-Export der Kennzahlen und Belege (Semikolon, Dezimalkomma, öffnet direkt in Excel).
- Reiter „Übersicht“: grobe Jahresübersicht der Einnahmen und Ausgaben (netto) je Monat mit Diagramm, Ergebnis,
  USt-Zahllast und größten Kunden und Lieferanten. Nur zum eigenen Überblick, keine Gewinnermittlung (EÜR).

## Starten

Mit Docker:

```sh
cp .env.example .env        # anpassen
mkdir -p rechnungen/ausgang rechnungen/eingang
docker compose up -d
```

Danach läuft die App auf <http://localhost:8000>.

Das Docker-Image baut die Oberfläche selbst mit (Node-Stufe im `Dockerfile`).

Ohne Docker (Python 3.11 oder neuer, Node 20 oder neuer für den Build der Oberfläche):

```sh
(cd frontend && npm ci && npm run build)   # schreibt nach ustva/static
pip install -e .
export RECHNUNGS_VERZEICHNIS=~/Rechnungen
python -m ustva serve          # Weboberfläche auf http://127.0.0.1:8000
```

Ohne gebaute Oberfläche läuft nur die API; die Startseite zeigt dann den Build-Befehl an.

## Monatlicher Ablauf

1. App öffnen. Ausgewählt ist automatisch der Monat, dessen Voranmeldung als Nächstes fällig ist.
2. „Rechnungen einlesen“ holt neue Belege aus Verzeichnis und Paperless. Schon eingelesene werden übersprungen.
3. Bezahlte Ausgangsrechnungen unter „Belege ohne Zeitraum“ anklicken und das Zahlungsdatum eintragen.
   Gelb markierte Belege anklicken, mit der Rechnung vergleichen, ggf. korrigieren und speichern (oder „OK“).
4. Hinweise lesen. Bei Fremdwährungen den BMF-Kurs des Monats eintragen.
5. In Mein ELSTER das Formular „Umsatzsteuer-Voranmeldung“ öffnen und die Kennzahlen eintragen. Werte mit
   „berechnet ELSTER“ rechnet ELSTER selbst aus; sie dienen zum Abgleich.
6. Übertragungsprotokoll speichern, Zahlung veranlassen, CSV für die eigene Ablage exportieren.

Für die Kommandozeile oder einen Cronjob am Monatsanfang:

```sh
python -m ustva bericht --einlesen            # nächste fällige Voranmeldung
python -m ustva bericht --zeitraum 2026-09
```

Der Befehl endet mit Status 2, wenn Belege nicht berücksichtigt werden konnten (z. B. fehlender Kurs).

## Rechnungsverzeichnis

```
rechnungen/
  ausgang/            eigene Rechnungen (Unterordner beliebig, z. B. ausgang/2026/)
  eingang/            Rechnungen von Lieferanten
```

Erkannt werden auch `ausgangsrechnungen`/`einnahmen` und `eingangsrechnungen`/`ausgaben`
(anpassbar über `ORDNER_AUSGANG` und `ORDNER_EINGANG`). Gescannte PDFs ohne Textebene lassen sich nicht auslesen;
dafür Paperless nutzen oder die Werte manuell eintragen.

### E-Rechnungen

Seit 2025 müssen Unternehmen in Deutschland E-Rechnungen empfangen können. Die App liest sie strukturiert aus:

- ZUGFeRD 2.x und Factur-X: PDF mit eingebetteter XML (`factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml`),
- XRechnung und andere Rechnungen nach EN 16931 als reine `.xml`-Datei, in CII- oder UBL-Syntax.

Rechnungsnummer, Datum, Partner, USt-IdNr., Währung und die Beträge je Steuersatz kommen direkt aus der XML.
Eine Rechnung mit 19 % und 7 % ergibt zwei Belege. Den Steuerfall leitet die App aus der Steuerkategorie ab
(`S` Inland, `AE` Reverse Charge, `K` innergemeinschaftlich, `G` Ausfuhr) und aus dem Land des Partners.
Gutschriften werden negativ übernommen. Solche Belege sind sofort geprüft, außer es bleibt etwas offen, z. B. der
angenommene Steuersatz bei Reverse Charge, ausländische Umsatzsteuer oder das Profil Minimum ohne Steueraufschlüsselung.
In der Belegliste tragen sie das Zeichen „E-Rechnung“. Ältere ZUGFeRD-1.0-Rechnungen werden wie normale PDFs gelesen.
Ist `EIGENE_USTID` gesetzt, warnt die App, wenn eine Rechnung im falschen Ordner liegt.

### Zusatzangaben

Optional kann neben einer Rechnung eine JSON-Datei mit festen Werten liegen, die die Erkennung überschreiben,
z. B. `openai-2026-09.pdf.json`:

```json
{"steuerfall": "ausland_13b", "netto": "20.00", "waehrung": "USD", "partner": "OpenAI", "geprueft": true}
```

## Kontoumsätze (N26)

In der N26-App oder im Web unter Konto → Kontoauszüge den CSV-Export für den Monat herunterladen und in der App unter
„Kontoumsätze“ hochladen. Alternativ die CSV-Dateien in den Unterordner `konto/` des Rechnungsverzeichnisses legen;
„Rechnungen einlesen“ nimmt sie dann mit. Mehrfaches Einlesen derselben Umsätze ist unschädlich.

Zugeordnet wird ein Umsatz einer Rechnung gleicher Richtung (Einnahme ↔ Ausgangsrechnung, Ausgabe ↔ Eingangsrechnung)
mit gleichem Zahlbetrag, gebucht höchstens 10 Tage vor bis 120 Tage nach dem Rechnungsdatum. Bei Fremdwährung zählt der
Originalbetrag aus dem Export (z. B. 20,00 USD bei OpenAI). Passt es nicht automatisch, lässt sich der Beleg in der
Liste von Hand zuordnen.

Umsätze ohne Beleg erscheinen als Warnung. Was keinen Beleg braucht (Steuerzahlung, Privates, Umbuchung), markierst du
als „kein Beleg nötig“, mit „Partner immer“ auch für alle künftigen Umsätze dieses Partners. Zahlungen ans Finanzamt
werden automatisch übergangen (`BANK_IGNORIEREN`).

## Paperless-ngx

1. In Paperless unter „Profil“ ein API-Token erzeugen und in der App unter „Einstellungen“ mit der URL eintragen
   (oder in `PAPERLESS_URL` und `PAPERLESS_TOKEN`). „Verbindung testen“ prüft beides.
2. Rechnungen mit dem Tag oder Dokumenttyp `Ausgangsrechnung` bzw. `Eingangsrechnung` versehen
   (Namen anpassbar über `PAPERLESS_TAGS_AUSGANG` und `PAPERLESS_TAGS_EINGANG`, mehrere mit Komma).
3. Optional benutzerdefinierte Felder anlegen; sie haben Vorrang vor dem OCR-Text:
   `Netto` oder `Brutto` (Geldfeld, Währung wird übernommen), `Umsatzsteuer`, `Steuersatz`, `Zahlungsdatum`.

Eingelesen werden Dokumente, deren Datum im gewählten Monat oder in den beiden Vormonaten liegt.
Bei PDF-Dokumenten lädt die App die Originaldatei und liest eine eingebettete ZUGFeRD-/Factur-X-Rechnung aus;
deren Angaben haben dann Vorrang vor Feldern und OCR-Text (Korrespondent und Zahlungsdatum aus Paperless bleiben).

Paperless selbst nächtlich verschlüsselt in eine Nextcloud sichern: [deploy/paperless-backup](deploy/paperless-backup/README.md).

## Unterlagen für die Steuerberatung

„ZIP für Steuerberatung“ in der Monatsansicht packt alles für den Monat zusammen:

- `Belege/`: jeder Beleg, der in der Voranmeldung zählt oder einem Kontoumsatz des Monats zugeordnet ist, als Datei
  aus dem Rechnungsverzeichnis bzw. als PDF aus Paperless (Name: Datum, Art, Partner, Betrag)
- `Kontoauszug/` und `Finanzamt/`: Dokumente aus Paperless mit dem Tag `Kontoauszug` bzw. `UStVA-Protokoll`
  (anpassbar in den Einstellungen), deren Titel oder Text den Monat nennt, z. B. „09/2026“, „September 2026“ oder
  „UStVA_2026_-_September“. Ein Protokoll ohne erkennbaren Monat zählt zum Vormonat, wenn es bis zum 15. erstellt wurde.
  Der Kontoauszug muss bis zum 15. des Folgemonats datiert sein, das Protokoll bis Ende des übernächsten Monats.
- die beiden CSV-Exporte und, falls nötig, `INFO.txt`: was fehlt (auch Kontoumsätze ohne Beleg), als
  Belege der Voranmeldung, die in einem anderen Monat abgebucht wurden, als „Fehlbuchung/privat“ markierte
  Kontoumsätze mit Notiz und die übrigen Umsätze, die keinen Beleg brauchen

## Einstellungen und Anmeldung

Im Reiter „Einstellungen“ lassen sich Paperless (URL, API-Token, Tags), die eigene USt-IdNr. und das Bundesland
ändern. Diese Werte liegen in der Datenbank und haben Vorrang vor der `.env`-Datei; ein geleertes Feld lässt wieder
den Wert aus der `.env` gelten. Das Paperless-Token wird nach dem Speichern nicht mehr angezeigt.

Dort lässt sich auch das Passwort ändern oder, falls noch keins gesetzt ist, ein erstes festlegen. Gespeichert wird
nur ein PBKDF2-Hash; er gilt statt `APP_BENUTZER`/`APP_PASSWORT`. Passwort vergessen:

```sh
python -m ustva passwort                  # neues Passwort setzen (fragt zweimal)
python -m ustva passwort --zuruecksetzen  # wieder APP_BENUTZER/APP_PASSWORT aus der .env verwenden
```

Der laufende Dienst übernimmt die Änderung sofort. Im Proxmox-Container:

```sh
runuser -u finanzomat -- env DATENBANK=/var/lib/finanz-o-mat/finanzomat.sqlite \
  /opt/finanz-o-mat/.venv/bin/python -m ustva passwort
```

## Umzug aus sevDesk

`python -m ustva sevdesk sichern|importieren|abgleich` sichert alle Daten und PDFs aus sevDesk, legt die Dateien in
Paperless ab, übernimmt die Buchungen als geprüfte Belege und vergleicht die Zahllast je Monat mit den abgegebenen
Voranmeldungen. Anleitung: [docs/sevdesk-migration.md](docs/sevdesk-migration.md).

## Annahmen und Grenzen

- Standard ist Ist-Versteuerung: Steuerpflichtige Inlandsumsätze zählen erst im Monat des Zahlungseingangs.
  Ausgangsrechnungen brauchen deshalb ein Zahlungsdatum (am Beleg, im Paperless-Feld `Zahlungsdatum` oder in der
  JSON-Datei); bis dahin stehen sie unter „Belege ohne Zeitraum“. Vorsteuer und Reverse Charge richten sich weiter
  nach der Rechnung. Mit `VERSTEUERUNG=soll` zählt stattdessen das Rechnungsdatum.
- Standard ist keine Dauerfristverlängerung (`DAUERFRISTVERLAENGERUNG=true` schaltet sie ein).
- Das Rechnungsdatum gilt als Leistungsdatum. Weicht der Leistungsmonat ab, das Datum am Beleg anpassen.
- Bei Reverse Charge nimmt die App 19 % an und geht von vollem Vorsteuerabzug aus (Kz 67 = Kz 47 + Kz 85).
- Bemessungsgrundlagen werden auf volle Euro abgerundet, die Steuer für Kz 81/86/89/93 daraus berechnet, wie ELSTER es tut.
- Nicht abgedeckt: Kleinunternehmer, Differenzbesteuerung, Einfuhrumsatzsteuer (Kz 62), Vorsteuerberichtigung,
  Steuersatz 0 % (Kz 87), Bauleistungen (Kz 73/74), Sondervorauszahlung (Kz 39), die ZM-Übermittlung selbst.
- Die automatische Erkennung ist eine Heuristik. Jeder eingelesene Beleg bleibt als „prüfen“ markiert, bis du ihn bestätigst.

## Entwicklung

Aufbau:

- `ustva/`: Backend in Python mit FastAPI (Berechnung, Import, Datenbank, JSON-API unter `/api`).
- `frontend/`: Oberfläche in Vue 3 mit TypeScript, Tailwind CSS 4 und PrimeVue 4 (Theme Aura, folgt dem Hell-/Dunkelmodus
  des Systems), gebaut mit Vite. Das Build-Ergebnis landet in `ustva/static` und ist nicht eingecheckt.

```sh
pip install -e ".[dev]"
pytest
ruff check .

cd frontend
npm ci
npm run dev        # Vite auf http://localhost:5173, leitet /api an python -m ustva serve (Port 8000) weiter
npm run build      # Typprüfung (vue-tsc) und Build nach ustva/static
```

Die API-Dokumentation liegt unter `/api/docs`.
