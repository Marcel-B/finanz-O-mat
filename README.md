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
- Konten bei N26, ING, bunq u. a. automatisch über Enable Banking abrufen; Reiter „Konten“ mit Saldo und Umsätzen
  aller Konten als eigene Übersicht.
- CSV-Export der Kennzahlen und Belege (Semikolon, Dezimalkomma, öffnet direkt in Excel).
- Reiter „Monatsabschluss“: führt auf Wunsch Schritt für Schritt durch das Monatsende, siehe „Monatlicher Ablauf“.
- Reiter „Übersicht“: grobe Jahresübersicht der Einnahmen und Ausgaben (netto) je Monat mit Diagramm, Ergebnis,
  USt-Zahllast und größten Kunden und Lieferanten. Nur zum eigenen Überblick, keine Gewinnermittlung (EÜR).
- Reiter „Tätigkeiten“: Tätigkeitsnachweis für den Kunden, siehe unten.
- Reiter „Stundenzettel“: feste Stunden je Monat an einem festen Wochentag, siehe unten.
- Reiter „Adressbuch“: alle Kunden und Lieferanten mit Anschrift, USt-IdNr., Kundennummer und Anzahl der Belege.
  Neue Partner aus Belegen kommen von selbst dazu; Anschriften stammen aus E-Rechnungen und dem sevDesk-Umzug oder
  werden von Hand ergänzt. Doppelte Schreibweisen lassen sich zusammenführen, CSV-Export für Excel. Automatische
  Quellen füllen nur leere Felder, eigene Einträge bleiben stehen.

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
2. „Rechnungen einlesen“ holt neue Belege aus Verzeichnis und Paperless. Schon eingelesene werden übersprungen,
   ebenso Dokumente mit einer schon erfassten Rechnung (gleiche Art, Datum, Betrag und Rechnungsnummer, ohne Nummer
   gleicher Partner), etwa wenn dieselbe Rechnung unter anderem Titel zweimal in Paperless liegt. Die Meldung nennt
   beide Dokumente. Schon erfasste Doppel zeigt die Monatsansicht als Warnung.
3. Bezahlte Ausgangsrechnungen unter „Belege ohne Zeitraum“ anklicken und das Zahlungsdatum eintragen.
   Gelb markierte Belege anklicken, mit der Rechnung vergleichen, ggf. korrigieren und speichern (oder „OK“).
   Über den Belegen und den Kontoumsätzen filtert je ein Suchfeld die Liste des Monats (Partner, Text,
   Rechnungsnummer, Betrag wie „62,89“, Datum wie „22.09.2026“; mehrere Wörter müssen alle vorkommen).
4. Hinweise lesen. Bei Fremdwährungen den BMF-Kurs des Monats eintragen.
5. In Mein ELSTER das Formular „Umsatzsteuer-Voranmeldung“ öffnen und die Kennzahlen eintragen. Werte mit
   „berechnet ELSTER“ rechnet ELSTER selbst aus; sie dienen zum Abgleich.
6. Übertragungsprotokoll speichern, Zahlung veranlassen, CSV für die eigene Ablage exportieren.

### Geführt: Reiter „Monatsabschluss“

Wer mag, startet den Abschluss im Reiter „Monatsabschluss“. Ein Stepper führt durch acht Schritte, jeder lässt
sich überspringen, und alles geht weiterhin auch über die anderen Reiter:

1. **Kontoauszug**: öffnet das N26-Webbanking; die CSV kommt in die Kontoumsätze, die PDF nach Paperless (Tag
   Kontoauszug, Titel „Kontoauszug September 2026“).
2. **Belege**: „Rechnungen einlesen“ wie in der Voranmeldung.
3. **Prüfen**: Umsätze ohne Beleg, ungeprüfte Belege und Hinweise; ein Klick öffnet den Monat in der Voranmeldung.
4. **ELSTER**: öffnet Mein ELSTER und zeigt die Kennzahlen nach Abschnitten des Formulars, mit Kopierknopf und
   Kz 83 zum Abgleich.
5. **Protokoll**: das Übertragungsprotokoll (PDF) nach Paperless (Tag UStVA-Protokoll); die Karte „Nächste Abgabe“
   springt danach auf den nächsten Monat.
6. **Steuerberatung**: ZIP herunterladen und einen E-Mail-Entwurf im Mailprogramm öffnen (`mailto:`). Empfänger,
   Betreff und Text stehen in einer Vorlage (Platzhalter `{monat}`, `{jahr}`), die im Schritt einmal eingetragen
   wird. Die ZIP hängt man selbst an; die App verschickt nichts.
7. **Drucken**: die Belege des Monats als ein PDF in einem neuen Tab („Ungedruckte“ oder „Alle“). Gedruckte
   Belege bekommen einen Vermerk mit Datum, zu sehen hier, in der Belegliste der Voranmeldung (Druckersymbol) und
   im Beleg. Den Druck gibt es auch als Knopf „Belege drucken“ in der Voranmeldung.
8. **Stundenzettel**: trägt den Vorschlag für den Monat ein, Excel herunterladen.

Erledigte Schritte bekommen einen Haken, soweit die App es prüfen kann (Kontoumsätze und Kontoauszug da, alles
zugeordnet und geprüft, Protokoll in Paperless, alles gedruckt, Stunden eingetragen).

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

### Automatisch abrufen (Enable Banking)

Statt der CSV-Datei kann die App die Umsätze über [Enable Banking](https://enablebanking.com) direkt bei der Bank
abrufen (PSD2, nur lesend), etwa bei N26, ING und bunq. Für die eigenen Konten ist das kostenlos („restricted mode“).
Verbunden wird über die Kommandozeile. Alle verbundenen Konten erscheinen mit Saldo und Umsätzen im Reiter „Konten“,
einer reinen Übersicht ohne Bezug zur Buchhaltung. In die Kontoumsätze der Buchhaltung (Belegabgleich, Warnung bei
fehlenden Belegen) kommen nur Konten, die mit `bank konto … --an` eingeschaltet sind.

Einrichten, einmalig:

1. Bei enablebanking.com registrieren, im Control Panel unter „API applications“ eine Anwendung für **Production**
   anlegen. Als Rückleitungsadresse (Redirect URL) die App mit einem freien Pfad eintragen, z. B.
   `https://finanz-o-mat.<tailnet>.ts.net/bank-verbunden`; die Seite muss nichts können. Für die Links zu
   Datenschutz und Nutzungsbedingungen reicht eine beliebige `https://`-Adresse.
2. Den privaten Schlüssel (`.pem`) beim Anlegen herunterladen und in den Container legen, z. B. nach
   `/etc/finanz-o-mat/enablebanking.pem` (`chown root:finanzomat`, `chmod 640`). Er liegt nicht im Backup; geht er
   verloren, im Control Panel eine neue Anwendung anlegen.
3. Bei der Anwendung „Activate by linking accounts“ wählen und die eigenen Konten verknüpfen. Nur verknüpfte Konten
   lassen sich im kostenlosen Modus abrufen.
4. In `/etc/finanz-o-mat/finanz-o-mat.env` (bzw. `.env`) eintragen:
   `ENABLE_BANKING_APP_ID="<Application ID>"` und `ENABLE_BANKING_SCHLUESSEL="/etc/finanz-o-mat/enablebanking.pem"`.

Verbinden und abrufen (im Container mit `finanz-o-mat …`, sonst `python -m ustva …`):

```sh
finanz-o-mat bank banken --suche ing         # genauen Namen der Bank finden
finanz-o-mat bank verbinden --bank N26       # Adresse öffnen, bei der Bank freigeben, Rückleitungsadresse einfügen
finanz-o-mat bank abrufen --konto DE… --trocken  # Probelauf: Umsätze nur zeigen, nichts speichern
finanz-o-mat bank konto DE… --an             # Konto in die Buchhaltung übernehmen (neue Konten nur in der Übersicht)
finanz-o-mat bank konto DE… --name ING       # Name, unter dem die Umsätze in der Buchhaltung erscheinen
finanz-o-mat bank abrufen                    # Saldo und gebuchte Umsätze aller Konten holen, Belege abgleichen
finanz-o-mat bank konten                     # Konten, Saldo, Freigabe, letzter Abruf, freie Abrufe
```

- Nach der Freigabe leitet die Bank auf die Rückleitungsadresse um. Die Seite zeigt „Not Found“, das ist in Ordnung:
  die ganze Adresse (mit `?code=…`) aus der Adresszeile kopieren und einfügen.
- Neu verbundene Konten stehen nur in der Kontenübersicht, damit private Konten nicht in die Umsatzsteuer geraten.
  Beim Einschalten übernimmt die App die schon abgerufenen Umsätze in die Buchhaltung. Umsätze von N26 erscheinen
  dort unter dem Kontonamen „N26“ wie beim CSV-Import.
- Die Freigabe gilt so lange, wie die Bank erlaubt (meist 90 oder 180 Tage). Danach einfach erneut `bank verbinden`;
  Name und Schalter der Konten bleiben. `bank abrufen` warnt zwei Wochen vorher.
- Der erste Abruf holt 90 Tage, danach ab einer Woche vor dem letzten Buchungstag (`--von JJJJ-MM-TT` für mehr, soweit
  die Bank liefert). Vorgemerkte Umsätze stehen schraffiert oben in der Kontenübersicht und werden bei jedem
  Abruf ersetzt; in die Buchhaltung kommen sie erst, wenn die Bank sie bucht.
- Der Dienst ruft selbst ab, zu den Uhrzeiten in `KONTOABRUF_ZEITEN` (Standard `09:00,12:30,16:00,19:30`, deutsche
  Zeit, nachts nicht; `aus` = nur von Hand). War der Dienst zur Abrufzeit aus, holt er den Abruf bis zu einer Stunde
  später nach. Ohne Anmeldung bei der Bank erlaubt PSD2 höchstens vier Abrufe je Konto in 24 Stunden; die App zählt
  mit und lässt weitere aus, auch beim Knopf „Jetzt abrufen“ im Reiter „Konten“ und bei `bank abrufen`. Ein Klick auf
  den Knopf verbraucht also einen der vier, dann fällt der nächste geplante Abruf unter Umständen aus.
- **Alte Auszüge einlesen:** Was weiter zurückliegt, als die Bank liefert, kommt als CAMT.053-Datei dazu, etwa aus
  MoneyMoney (Ablage → Exportieren → CAMT.053): im Reiter „Konten“ über „Auszüge einlesen“ oder im Container mit
  `finanz-o-mat bank einlesen datei.xml` (`--trocken` zählt nur). Die Umsätze gehen in die Kontenübersicht des
  verbundenen Kontos mit derselben IBAN, nicht in die Buchhaltung (beim Einschalten des Kontos aber mit). Was schon
  abgerufen ist, kommt nicht doppelt (gleicher Betrag, Buchungstag bis drei Tage auseinander), und ein späterer
  Abruf übernimmt eingelesene Zeilen. Dieselbe Datei zweimal einzulesen schadet nicht.
- Schon per CSV eingelesene Umsätze werden nicht doppelt angelegt (gleiches Konto, gleicher Betrag, Buchungstag bis
  drei Tage auseinander); Zuordnung und Markierung bleiben. Umgekehrt überspringt der CSV-Import abgerufene Umsätze.

### Depot der ING (FinTS)

Depots gehören nicht zu PSD2, Enable Banking liefert sie deshalb nicht. Den Bestand des ING-Direkt-Depots holt die App
über FinTS (HBCI), das die ING kostenlos anbietet: je Wertpapier Name, ISIN, Stück, Kurs, Wert und Einstandskurs.
Depotumsätze bietet die ING über FinTS nicht an. Das Depot steht im Reiter „Konten“ unter „Depots“, mit Gewinn oder
Verlust je Position, und hat mit der Buchhaltung nichts zu tun.

1. Eine FinTS-Produkt-ID bei der Deutschen Kreditwirtschaft beantragen
   (https://www.fints.org/de/hersteller/produktregistrierung, kostenlos, dauert bis zu zwei Wochen).
2. Im Container `finanz-o-mat-fints` aufrufen: fragt Produkt-ID, Benutzername und Passwort des Online-Bankings ab
   (wie beim Login im Browser; vor der Umstellung der ING 2026 Zugangsnummer und Internetbanking-PIN, nie App-PIN
   oder DiBa-Key), trägt sie in `/etc/finanz-o-mat/finanz-o-mat.env` ein (`FINTS_PRODUKT_ID`, `ING_FINTS_ZUGANG`,
   `ING_FINTS_PIN`; die Datei ist nur für den Dienst lesbar), startet den Dienst neu und ruft auf Wunsch gleich ab.
   Von Hand geht es genauso, ohne Container in der `.env`.
3. Einmal von Hand abrufen, im Reiter „Konten“ („Depots“ → „Abrufen“) oder im Container mit `finanz-o-mat bank depot`,
   und den Zugriff in der ING-App freigeben.

- Danach holt der Dienst den Bestand zu denselben Zeiten wie die Konten (`KONTOABRUF_ZEITEN`).
- Verlangt die ING eine Freigabe, wartet der Abruf bis zu zwei Minuten darauf. Kommt sie im Hintergrund nicht, fragt
  der Dienst nicht erneut (sonst käme bei jedem Abruf eine Anfrage aufs Telefon), bis ein Abruf von Hand sie wieder
  bekommen hat. Alle 90 Tage verlangt die ING außerdem einen Login im Online-Banking.
- Lehnt die ING die Anmeldung ab, steht ihre Meldung mit Code im Fehler; danach fragen Hintergrund und Öffnen des
  Reiters nicht mehr an, bis ein Abruf von Hand geklappt hat (sonst droht die Sperre nach mehreren Fehlversuchen).
- Die App merkt sich die Systemkennung und die Bankdaten von FinTS in der Datenbank (ohne PIN) und je Tag den Wert
  des Depots.

### ING-Konten sofort (FinTS)

Über Enable Banking erlaubt PSD2 nur vier Abrufe je Konto in 24 Stunden; nach einer Push-Nachricht der ING steht die
Buchung dann oft erst Stunden später in der Übersicht. Über denselben FinTS-Zugang wie das Depot holt die App Saldo,
Buchungen und vorgemerkte Buchungen der ING-Konten ohne dieses Limit:

- beim Öffnen des Reiters „Konten“ und wenn die App wieder in den Vordergrund kommt (höchstens alle zwei Minuten),
- mit „Jetzt abrufen“ (erst Enable Banking, dann FinTS),
- im Dienst stündlich von 7 bis 22 Uhr,
- im Container mit `finanz-o-mat bank ing`.

Abgerufen werden nur ING-Konten, die schon über Enable Banking verbunden sind (gleiche IBAN), die letzten 14 Tage. Sie
landen nur in der Kontenübersicht; in die Buchhaltung bringt sie weiter Enable Banking. Dessen nächster Abruf
übernimmt die Zeile, die schon über FinTS kam (gleicher Betrag, Buchungstag bis drei Tage auseinander), und FinTS legt
nichts an, was Enable Banking schon geliefert hat. Wartet die ING auf eine Freigabe in der App, fragen das Öffnen und
der Hintergrund nicht erneut nach, bis „Jetzt abrufen“ sie wieder bekommen hat.

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

**Doppelte Rechnungen** (z. B. einmal aus der Mail, einmal von Hand oder aus sevDesk) erkennt Paperless nur, wenn die
Dateien byte-gleich sind. finanz-O-mat übernimmt so eine Rechnung beim Einlesen nur einmal und meldet den Rest.
Aufräumen in Paperless:

```sh
finanz-o-mat paperless duplikate             # Liste: welche Rechnung mehrfach liegt, was behalten, was gelöscht würde
finanz-o-mat paperless duplikate --loeschen  # dieselbe Liste, nach Rückfrage löschen
```

Erkannt wird wie beim Einlesen an Art, Datum, Betrag und Rechnungsnummer (ohne Nummer am Partner), außerdem an
gleichem Text. Behalten wird das Dokument, auf das ein Beleg in finanz-O-mat verweist, sonst das älteste; es bekommt
die Tags der anderen. Gelöschtes landet im Papierkorb von Paperless. Mit `--von 2026-01` nur neuere Dokumente.

Rechnungen automatisch aus dem Postfach und aus einem Nextcloud-Ordner nach Paperless holen:
[deploy/paperless-eingang](deploy/paperless-eingang/README.md).

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

Angemeldet wird über ein Formular in der App. Danach trägt der Browser ein Sitzungs-Cookie (HttpOnly,
SameSite=Strict, über HTTPS zusätzlich Secure); in der Datenbank steht nur dessen Hash.

- Eine Sitzung endet nach 8 Stunden ohne Nutzung, spätestens nach 7 Tagen, beim Abmelden (Knopf oben rechts) und
  sobald sich Benutzer oder Passwort ändern, auch über die Kommandozeile oder die `.env`. Wer das Passwort in der App
  ändert, bleibt angemeldet; alle anderen Browser müssen sich neu anmelden.
- Nach 5 falschen Versuchen von einer Adresse innerhalb von 15 Minuten ist die Anmeldung von dort 15 Minuten
  gesperrt, nach 20 falschen Versuchen insgesamt für alle. Fehlversuche stehen im Log
  (`journalctl -u finanz-o-mat`). Ein Neustart des Dienstes hebt eine Sperre auf.
- Ändernde Anfragen von fremden Webseiten lehnt die App ab, die Seiten lassen sich nicht in fremde Seiten einbetten.

### Anmeldung über den Identity-Server

Statt mit Passwort meldet man sich über den eigenen Identity-Server an (OpenID Connect,
[Marcel-B/Identity](https://github.com/Marcel-B/Identity), Anleitung dort in `docs/login-integration.md`). Das
Backend ist ein vertraulicher Client: Es macht den Authorization Code Flow mit PKCE, prüft das ID-Token und gibt dem
Browser danach wie oben nur das Sitzungs-Cookie. Tokens sieht der Browser nie.

Einrichten im Identity-Server (Verwaltung unter `<Issuer>/Admin`):

| Was | Wert |
| --- | --- |
| Rolle | `finanzen` anlegen und dem eigenen Konto geben |
| Client-ID | `finanz-o-mat`, vertraulich (Secret erzeugen), Zustimmung `implicit` |
| Grant Types | `authorization_code`, `refresh_token` |
| Redirect-URI | `https://finanz-o-mat.<tailnet>.ts.net/api/identity/rueckruf` |
| Post-Logout-Redirect-URI | `https://finanz-o-mat.<tailnet>.ts.net/` |
| Scopes | keine zusätzlichen (`openid profile email roles offline_access` sind automatisch erlaubt) |

Dann in der `.env` bzw. `/etc/finanz-o-mat/finanz-o-mat.env` `IDENTITY_AUTHORITY`, `IDENTITY_CLIENT_SECRET` und
`APP_ADRESSE` setzen (siehe `.env.example`) und den Dienst neu starten. Die App muss unter `APP_ADRESSE` per HTTPS
erreichbar sein (`TAILSCALE_SERVE=ja`) und den Identity-Server erreichen. Wer die App über `http://<IP>:8000` öffnet,
wird zum Anmelden auf die HTTPS-Adresse geschickt.

- Herein kommen nur Konten mit der Rolle `IDENTITY_ROLLE` (Standard `finanzen`). Beim ersten Mal merkt sich die App
  das Konto (`sub`); danach kommt nur noch dieses Konto herein, auch wenn ein anderes die Rolle bekommt.
  `python -m ustva identity` zeigt die Bindung, `python -m ustva identity --zuruecksetzen` löst sie.
- Alle 15 Minuten gleicht die App eine Sitzung über das Refresh Token mit dem Server ab. Ist das Konto gesperrt, das
  Passwort geändert, die Rolle entzogen oder „überall abmelden“ gewählt, endet die Sitzung. Ist der Server nur nicht
  erreichbar, bleibt sie bestehen.
- Abmelden beendet die Sitzung, widerruft das Refresh Token und meldet auch beim Identity-Server ab.
- Passwort, Zwei-Faktor-Anmeldung und Passkeys verwaltet man im Identity-Server; das Passwort der App gilt nicht.
- **Notausgang:** Ist der Identity-Server kaputt, `IDENTITY_AUTHORITY` in der Env-Datei auskommentieren und den Dienst
  neu starten (`systemctl restart finanz-o-mat`). Dann gelten wieder Passwort bzw. `APP_BENUTZER`/`APP_PASSWORT`.
  Jeder Wechsel beendet alle Sitzungen.

## Monitoring

Die App lässt sich an den gemeinsamen Monitoring-Stack (Grafana, Prometheus, Loki; Steckbrief in
[Marcel-B/scripts → docs/monitoring.md](https://github.com/Marcel-B/scripts/blob/main/docs/monitoring.md)) anbinden.
Im Proxmox-Container erledigt das `finanz-o-mat-lxc.sh monitoring <CTID>` (siehe
[deploy/proxmox/README.md](deploy/proxmox/README.md#monitoring)); in Grafana gibt es dann das Dashboard
**Finanz-O-Mat**.

**Messwerte:** `GET /metrics` im Format von Prometheus, ohne Anmeldung, damit Prometheus abrufen kann. Deshalb
stehen dort nur Anzahlen und Laufzeiten, keine Beträge, Namen oder Rechnungsnummern.

| Messwert | Bedeutung |
|---|---|
| `finanzomat_http_requests_total{method,route,status}` | Anfragen je Route (Pfadvorlage wie `/api/belege/{beleg_id}`) |
| `finanzomat_http_request_duration_seconds{route}` | Dauer der Anfragen |
| `finanzomat_import_belege_total{quelle,ergebnis}` | eingelesene Belege und Kontoumsätze (neu, vorhanden, fehler, …) |
| `finanzomat_anmeldungen_total{ergebnis}` | Anmeldeversuche: ok, falsch, gesperrt |
| `finanzomat_log_lines_total{source,level}` | Protokollzeilen, für Warnungen und Fehler im Dashboard |
| `finanzomat_belege{art}`, `finanzomat_belege_zu_pruefen`, `finanzomat_umsaetze_ohne_beleg` | Bestand beim Abruf |
| `finanzomat_backup_ok`, `finanzomat_backup_last_success_timestamp_seconds` | Stand des Nextcloud-Backups (nur im Container) |
| `finanzomat_update_available` | 1, wenn eine neue Version bereitsteht (nur im Container) |
| `finanzomat_loki_dropped_total` | Zeilen, die Loki nicht erreicht haben |
| `finanzomat_kontoabruf_total{bank,ausloeser,ergebnis}` | Kontoabrufe über Enable Banking: `zeitplan` oder `knopf`; ok, fehler, uebersprungen |
| `finanzomat_kontoabruf_umsaetze_total{bank,ziel}` | neu abgerufene Umsätze, `uebersicht` (Reiter „Konten“) oder `buchhaltung` |
| `finanzomat_enable_banking_requests_total{endpunkt,status}`, `…_request_duration_seconds{endpunkt}` | Anfragen an die API von Enable Banking (`accounts/transactions`, `accounts/balances`, …) |
| `finanzomat_bankkonto_info{bank,konto,buchhaltung}` | je verbundenem Konto; `konto` ist der Bankname, bei mehreren Konten einer Bank mit Nummer (keine IBAN) |
| `finanzomat_bankkonto_freigabe_tage`, `…_freigabe_bis_timestamp_seconds` | Restlaufzeit der Freigabe der Bank, für einen Alarm vor dem Ablauf |
| `finanzomat_bankkonto_letzter_abruf_timestamp_seconds`, `…_abrufe_frei`, `…_fehler`, `…_umsaetze` | letzter erfolgreicher Abruf, freie Abrufe in 24 Stunden, Fehler beim letzten Abruf, Umsätze in der Übersicht (kein Saldo) |
| `finanzomat_bankkonto_fints_abruf_timestamp_seconds` | letzter erfolgreicher Abruf über FinTS (nur ING) |
| `finanzomat_ing_fints_abruf_total{ausloeser,ergebnis}`, `finanzomat_ing_fints_umsaetze_total` | Abrufe der ING-Konten über FinTS (oeffnen, knopf, zeitplan) und neu gekommene Umsätze |
| `finanzomat_depotabruf_total{ausloeser,ergebnis}` | Depotabrufe über FinTS: ok, fehler, freigabe (App-Freigabe fehlt), uebersprungen |
| `finanzomat_depot_positionen{bank,depot}`, `…_letzter_abruf_timestamp_seconds` | Wertpapiere je Depot und letzter erfolgreicher Abruf (kein Wert, keine Depotnummer) |
| `finanzomat_depot_freigabe_noetig`, `finanzomat_depot_fehler` | 1, wenn der Depotabruf auf eine Freigabe in der ING-App wartet bzw. zuletzt fehlschlug |
| `process_*`, `python_*` | Speicher, CPU und Garbage Collector des Prozesses |

**Protokoll:** Alles, was die App und uvicorn protokollieren (Start, Zugriffe außer `/metrics`, Importe,
Fehlversuche bei der Anmeldung, Fehler), geht wie bisher ins Journal und mit `LOKI_URL` (z. B.
`http://monitoring:3100`) zusätzlich an Loki, mit den Labels `app="finanzomat"`, `host` (`LOKI_HOST`), `level` und
`source` (`server`, `zugriff`, `web`, `paperless`, …). Ein eigener Thread schickt die Zeilen alle zwei Sekunden
gesammelt; ist Loki nicht erreichbar, verwirft er sie und meldet das höchstens alle fünf Minuten im Journal. Die
Stufe fürs Protokoll setzt `LOG_LEVEL` (Standard `INFO`).

## Tätigkeitsnachweis

Im Reiter „Tätigkeiten“ trägst du je Tag ein, wie viele Stunden du für welchen Auftrag was gemacht hast. Ein neuer
Eintrag ist mit dem letzten vorbelegt (Auftrag, Ticket, Tätigkeit, Rolle, Ort, Leistungsart), das Datum springt auf den
nächsten freien Werktag. Frühere Tätigkeiten schlägt das Feld beim Tippen vor, Werktage ohne Eintrag stehen unter dem
Formular. Feiertage (bundesweit und im eingestellten Bundesland) und Urlaub zählen dabei nicht als fehlend, sondern
stehen darunter als „Frei“.

Urlaub kommt aus einem verknüpften Kalender (Einstellungen → „Kalender für Urlaub“, oder `KALENDER_URL`): in Nextcloud
im Kalender „Teilen“ → „Link teilen“ und den Link einfügen, die App macht daraus die Export-Adresse; sonst geht jede
ICS- oder webcal-Adresse. Es zählen nur ganztägige Termine ohne Wiederholung, Termine mit Uhrzeit (Besprechungen) und
Serien (Geburtstage) nicht. Mit Stichwörtern („Urlaub, Krank“) lässt sich auch der normale Kalender nehmen. Die App
liest den Kalender höchstens alle 15 Minuten; „Kalender testen“ liest ihn sofort neu. Mit „Excel erstellen“ gibt es den Monat als .xlsx im Layout des bisherigen Nachweises (Satz × Menge,
Summen, geleistete Stunden, Feld für die Unterschrift des Kunden).

Was gleich bleibt, steht in der Vorlage (Reiter „Tätigkeiten“ → „Vorlage“ oder unter „Einstellungen“): Stundensatz,
Name, Überschrift, Absender rechts oben, Aufträge mit Auftragsnummer und die Auswahllisten. „Excel-Datei übernehmen“
liest all das aus der bisherigen Excel-Datei (Blatt „Werte“ und die Monatsblätter) und übernimmt auch die schon
erfassten Monate. Der Stundensatz wird am Eintrag gespeichert; ein neuer Satz ändert also keine alten Monate.

## Stundenzettel

Für einen Stundenzettel mit festen Stunden je Monat (Standard: 20 Stunden, mittwochs). Der Reiter „Stundenzettel“
öffnet den vergangenen Monat mit einem Vorschlag: alle Mittwoche ohne Feiertag, die Stunden in halben Stunden
gleichmäßig verteilt (vier Mittwoche je 5 h, fünf je 4 h). Vor dem Eintragen lassen sich Tage entfernen, hinzufügen
oder verschieben und die Stunden ändern, etwa in der Urlaubszeit; „Gleichmäßig verteilen“ verteilt die Monatsstunden
neu auf die gewählten Tage. Ein Klick auf „… eintragen“ speichert den Monat.

„Excel erstellen“ gibt das Jahr als .xlsx im Layout der bisherigen Datei aus, ein Blatt je Monat von Januar bis zum
vergangenen Monat (den laufenden nur, wenn schon etwas eingetragen ist), also nichts in der Zukunft. Über der
Unterschriftslinie steht „Ort, Datum“ mit dem Monatsende als Datum. Name, Ort, Monatsstunden und Arbeitstag stehen unter „Einstellungen“ im Reiter; dort lässt sich auch die bisherige Excel-Datei
übernehmen. Übernommen werden nur Zeilen mit einem Datum aus dem gewählten Jahr, Blätter mit alten Daten meldet die App.

## Umzug aus sevDesk

Unter Einstellungen > „Umzug aus sevDesk“ den Export-Ordner aus sevDesk hineinziehen, den Probelauf ansehen und
übernehmen: Die Dateien kommen nach Paperless, die Buchungen als geprüfte Belege in die App, Kunden und Lieferanten
ins Adressbuch, und die Zahllast je Monat steht neben der abgegebenen. Auf der Kommandozeile geht dasselbe mit
`finanz-o-mat sevdesk importieren|abgleich` (auch mit einer Sicherung über die API, `sevdesk sichern`).
Anleitung: [docs/sevdesk-migration.md](docs/sevdesk-migration.md).

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
