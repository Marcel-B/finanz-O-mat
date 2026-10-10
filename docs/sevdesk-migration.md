# Umzug aus sevDesk

Das Werkzeug holt alles aus sevDesk, legt die Belegdateien in Paperless ab und übernimmt die Buchungen als geprüfte
Belege in finanz-O-mat. Es arbeitet in drei Schritten:

| Schritt | Befehl | Was passiert |
|---|---|---|
| 1 | `sevdesk sichern` | Alle Daten (JSON) und alle Beleg-, Rechnungs- und Gutschrift-PDFs in einen Ordner. Nur lesend. |
| 2 | `sevdesk importieren --trocken` | Probelauf: zeigt, was übernommen würde, und die Zahllast je Monat. Schreibt nichts. |
| 2 | `sevdesk importieren` | Dateien nach Paperless, Buchungen nach finanz-O-mat. Wiederholbar. |
| 3 | `sevdesk abgleich` | Zahllast je Monat gegen die abgegebenen Voranmeldungen. |

Schritt 2 und 3 brauchen sevDesk nicht mehr, nur den Sicherungsordner.

## Vor dem Start

- **API-Token** aus sevDesk: Einstellungen > Benutzer > API-Token. Er wird nur für Schritt 1 gebraucht und nur als
  Umgebungsvariable `SEVDESK_TOKEN` übergeben (nicht auf der Kommandozeile, damit er nicht in der Shell-History landet).
- **Für die Steuerberaterin** zusätzlich in sevDesk selbst den DATEV-Export (mit Belegbildern) für die offenen
  Jahre ziehen. Den liest finanz-O-mat nicht, er ist ihr gewohntes Format.
- **Paperless** muss in finanz-O-mat eingerichtet sein (Einstellungen oder `.env`). Die Tags „Eingangsrechnung“,
  „Ausgangsrechnung“ und „sevDesk“ legt das Werkzeug an, wenn es sie nicht gibt; Korrespondenten ebenso.
- sevDesk erst kündigen, wenn Schritt 3 passt und die Sicherung zusätzlich auf der Nextcloud liegt. Der Ordner aus
  Schritt 1 ist dein Archiv für die Aufbewahrungsfrist.

## Im Proxmox-Container

Als root in der Container-Shell (`pct enter <CTID>`):

```sh
set -a; . /etc/finanz-o-mat/finanz-o-mat.env; set +a      # DATENBANK, Paperless usw.
read -rs SEVDESK_TOKEN && export SEVDESK_TOKEN              # Token einfügen, Enter
ustva() { runuser -p -u finanzomat -- /opt/finanz-o-mat/.venv/bin/python -m ustva "$@"; }
SICHERUNG=/var/lib/finanz-o-mat/sevdesk
```

### 1. Sichern

```sh
ustva sevdesk sichern --ordner $SICHERUNG
```

Der Ordner enthält danach:

- `sicherung.json`: Zeitpunkt, Anzahl je Datenart, Liste der Dateien mit SHA-256
- `Voucher.json`, `VoucherPos.json` (Belege), `Invoice.json`, `InvoicePos.json` (Rechnungen), `CreditNote*.json`,
  `Contact.json`, `ContactAddress.json`, `CommunicationWay.json` (Kunden und Lieferanten),
  `CheckAccount.json`, `CheckAccountTransaction.json` (Konten und Bankumsätze)
- `dateien/Voucher/<id>.pdf`, `dateien/Invoice/<id>.pdf`, `dateien/CreditNote/<id>.pdf`
- `kontakte.csv`: Kunden und Lieferanten für Excel (Semikolon), Grundlage für das spätere Rechnungsschreiben

Bricht der Lauf ab, einfach erneut starten: schon geladene Dateien werden übersprungen. Das Abrufen der
Rechnungs-PDFs markiert in sevDesk nichts als versendet.

### 2. Probelauf, dann übernehmen

```sh
ustva sevdesk importieren --ordner $SICHERUNG --trocken
ustva sevdesk importieren --ordner $SICHERUNG
```

Die Ausgabe zeigt, wie viele Belege übernommen werden, was übersprungen wird (Entwürfe, Vorlagen, Mahnungen),
was schon in finanz-O-mat steht, Belege mit Hinweis und am Ende die Zahllast je Monat.

- **Doppelte**: Steht ein Beleg mit gleicher Rechnungsnummer und gleichem Datum schon in finanz-O-mat (etwa
  September 2026, den du schon mit der App gemacht hast), wird er nicht noch einmal angelegt. Kennt Paperless die
  Datei schon, verweist der Beleg auf das vorhandene Dokument. Weil Texterkennung Rechnungsnummern manchmal anders
  liest, Monate, die du schon mit finanz-O-mat abgegeben hast, am besten ganz ausklammern.
- **Zeitraum**: `--von 2023-01 --bis 2026-08` begrenzt auf Rechnungsmonate.
- **Paperless** verarbeitet die Dateien im Hintergrund (Texterkennung). Das Werkzeug wartet bis zu 5 Minuten
  (`--warten`); was danach noch läuft, wird beim nächsten Start abgeschlossen. Bei einigen hundert Dateien den Befehl
  nach einer Weile wiederholen, bis kein „noch in Verarbeitung“ mehr erscheint.
- `--ohne-paperless` übernimmt nur die Buchungen (die PDFs bleiben im Sicherungsordner).

Übernommene Belege gelten als geprüft (sevDesk hat sie ja schon gebucht) und werden von „Ungeprüfte neu einlesen“
nicht überschrieben. Unsichere Fälle tragen einen Hinweis am Beleg und stehen in der Ausgabe.

### 3. Abgleich mit den abgegebenen Voranmeldungen

Lege eine CSV mit der abgegebenen Zahllast (Kz 83) je Monat an, z. B. aus Mein ELSTER (Übermittelte Formulare) oder
aus der Voranmeldungs-Ansicht in sevDesk:

```
Zeitraum;Zahllast
2024-01;1.234,56
2024-02;-12,30
```

```sh
ustva sevdesk abgleich --erwartet /tmp/zahllast.csv
```

Monate mit mehr als 1 € Abweichung (`--toleranz`) werden markiert. Die CSV geht auch schon beim Probelauf:
`importieren --trocken --erwartet /tmp/zahllast.csv`.

## Wie die Steuerregeln abgebildet werden

| sevDesk | finanz-O-mat | Kennzahlen |
|---|---|---|
| 1 Umsatzsteuerpflichtige Umsätze | Inland, steuerpflichtig | 81 / 86 |
| 2 Ausfuhren | Ausfuhrlieferung | 43 |
| 3 Innergemeinschaftliche Lieferungen | Innergemeinschaftliche Lieferung | 41 |
| 17 Nicht im Inland steuerbare Leistung | Leistung im Drittland | 45 |
| 21 Reverse Charge § 18b | Leistung an EU-Unternehmer | 21 |
| 9 Vorsteuerabziehbare Aufwendungen | Rechnung mit deutscher USt (auch EU-Anbieter mit USt, wie bisher) | 66 |
| 8 Innergemeinschaftliche Erwerbe | Innergemeinschaftlicher Erwerb | 89 / 93 / 61 |
| 12 Reverse Charge § 13b Abs. 2 | Drittland-Unternehmer, Reverse Charge | 84 / 85 / 67 |
| 14 Reverse Charge § 13b Abs. 1 EU | EU-Unternehmer, Reverse Charge | 46 / 47 / 67 |
| 10 Nicht vorsteuerabziehbar, 16 Nicht steuerbar (Ausgabe) | Ohne Umsatzsteuer | – |
| 22 Nicht steuerbar (Einnahme) | Nicht berücksichtigen | – |
| 4, 5, 11, 18–20 (§ 4, § 13b-Umsatz, Kleinunternehmer, OSS) | Nicht berücksichtigen, mit Hinweis | – |
| 13 Reverse Charge ohne Vorsteuerabzug | Drittland-Unternehmer, Reverse Charge, mit Hinweis | 84 / 85 / 67 |

Ältere Belege von vor dem sevDesk-Update 2.0 haben statt der Regel einen Steuertyp (`default`, `eu`, `noteu`, `ss`);
die werden sinngemäß genauso abgebildet. Hat ein Beleg mehrere Steuersätze, entsteht je Satz ein Beleg.
Fremdwährungsbelege werden mit den Euro-Beträgen übernommen, die sevDesk gebucht hat.

## Was nicht übernommen wird

- **Entwürfe**, Vorlagen für wiederkehrende Belege und Rechnungen, Mahnungen.
- **Gutschriften** (CreditNote): gesichert, aber nicht als Beleg angelegt; die Ausgabe listet sie zum Nachtragen.
- **Bankumsätze**: gesichert in `CheckAccountTransaction.json`, aber nicht als Kontoumsätze angelegt. Für den
  Kontoabgleich in der App die N26-CSV der gewünschten Monate importieren.
- **Teilzahlungen**: Es zählt das Zahlungsdatum aus sevDesk; solche Rechnungen tragen einen Hinweis.

## Mögliche Abweichungen beim Abgleich

- finanz-O-mat zieht Vorsteuer im Monat der Rechnung ab, Umsätze bei Ist-Versteuerung im Monat der Zahlung. Hat
  sevDesk die Vorsteuer nach Zahlungsdatum gebucht, verschiebt sich ein Betrag in den Nachbarmonat; die Summe über
  die Monate bleibt gleich.
- Bemessungsgrundlagen werden wie in ELSTER auf volle Euro abgerundet; das macht Cent-Differenzen.
- Belege, die in sevDesk erst nach der Abgabe korrigiert wurden, zeigen die korrigierten Werte.
