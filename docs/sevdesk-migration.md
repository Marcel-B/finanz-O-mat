# Umzug aus sevDesk

Es gibt zwei Wege, je nach sevDesk-Tarif:

- **Export-Dateien** (jeder Tarif): Belege und Rechnungen in der sevDesk-Oberfläche exportieren, den Ordner in den
  Container kopieren, übernehmen. → [Weg A](#weg-a-export-dateien)
- **API** (ab Tarif „Buchhaltung Pro“, mit API-Token): holt zusätzlich Steuerregeln, Kontakte und Bankumsätze.
  → [Weg B](#weg-b-über-die-api)

Beide Wege legen die Dateien in Paperless ab (Tags „Eingangsrechnung“ bzw. „Ausgangsrechnung“ und „sevDesk“,
Korrespondent, Datum), übernehmen die Buchungen als geprüfte Belege, sind wiederholbar und haben einen Probelauf.

## Weg A: Export-Dateien

### 1. In sevDesk exportieren

- **Belege**: Belege > alle auswählen (Zeitraum „Alle“) > Exportieren: CSV und „Belege als ZIP“.
- **Rechnungen**: Rechnungen > alle auswählen > Exportieren: CSV und „PDFs als ZIP“.
- **Kontakte** (optional, hilft bei der Einordnung ausländischer Anbieter): Kontakte > Exportieren als CSV.

Alles in einen Ordner legen und die ZIPs dort entpacken. Er sieht dann so aus:

```
sevDesk/
  voucher.csv
  invoices.csv
  contacts.csv
  Belege_11/       2024-03-15_Ausgabebeleg_<Belegnummer>_<…>.pdf
  Rechnungen_11/   2024-03-15_Rechnung_RE-1000_<…>.pdf
```

Die Dateinamen bitte nicht ändern: darüber findet das Werkzeug zu jeder Buchung ihre Datei.

### 2. Ordner in den Container kopieren

Vom Mac auf den Proxmox-Host, dort in den Container (`<CTID>` ist die Nummer des finanz-O-mat-Containers):

```sh
# auf dem Mac
cd ~/Downloads && tar czf sevdesk.tgz sevDesk && scp sevdesk.tgz root@<proxmox-host>:/tmp/
# auf dem Proxmox-Host
pct push <CTID> /tmp/sevdesk.tgz /var/lib/finanz-o-mat/sevdesk.tgz
pct enter <CTID>
# im Container
cd /var/lib/finanz-o-mat && tar xzf sevdesk.tgz && rm sevdesk.tgz && chown -R finanzomat: sevDesk
```

### 3. Probelauf, dann übernehmen

```sh
finanz-o-mat sevdesk importieren --ordner /var/lib/finanz-o-mat/sevDesk --trocken
finanz-o-mat sevdesk importieren --ordner /var/lib/finanz-o-mat/sevDesk
```

Die Ausgabe zeigt, wie viele Belege übernommen würden, was übersprungen wird, Belege mit Hinweis und am Ende die
Zahllast je Monat neben der **abgegebenen Zahllast**. Die nimmt das Werkzeug aus den Zahlungen ans Finanzamt im
Export (Kategorie „Umsatzsteuer-Vorauszahlungen“): maßgeblich ist der Text der Überweisung („Umsatzsteuer Mai 25“,
„4.Vj.23“), sonst die Belegnummer („UStVA-07.2026“), sonst der Monat vor der Zahlung. Vierteljährliche Abgaben
werden als Quartal verglichen. Wer die Werte aus Mein ELSTER nehmen will, gibt `--erwartet` an (siehe Weg B,
Schritt 3).

Kunden und Lieferanten aus `contacts.csv` (mit Anschrift, Kundennummer, USt-IdNr. und Ansprechpartner) und die
Empfänger der Rechnungen landen dabei im Adressbuch der App.

Doppelte, Zeitraum (`--von`/`--bis`), Paperless-Wartezeit und `--ohne-paperless` funktionieren wie bei Weg B.
**Monate, die schon mit finanz-O-mat gemacht sind, mit `--bis` ausklammern.**

Später noch einmal vergleichen (z. B. nachdem Belege in der App korrigiert wurden):

```sh
finanz-o-mat sevdesk abgleich --ordner /var/lib/finanz-o-mat/sevDesk
```

### Wie die Export-Zeilen eingeordnet werden

Die CSV hat keine Steuerregel, nur Kategorie und Steuersatz. Beträge sind brutto; Netto und Steuer rechnet das
Werkzeug je Position aus.

| Export | finanz-O-mat | Kennzahlen |
|---|---|---|
| Kategorie „Erlöse …“ oder Einnahmebeleg, mit Steuersatz | Inland, steuerpflichtig (zählt im Monat der Zahlung) | 81 / 86 |
| Rechnungen aus `invoices.csv` | Inland, steuerpflichtig; offene Rechnungen ohne Zahlungsdatum | 81 / 86 |
| Ausgabe mit 19 % oder 7 % (auch EU-Anbieter mit deutscher USt) | Rechnung mit deutscher USt | 66 |
| Ausgabe mit 0 %, Löhne, Versicherung, Kontoführung, Abgaben | Ohne Umsatzsteuer | – |
| Ausgabe mit 0 % bei Anbieter im Ausland (Name, Land oder USt-IdNr. im Kontakt) | Reverse Charge EU bzw. Drittland, mit Hinweis | 46/47 bzw. 84/85, 67 |
| Privatentnahmen, Einkommensteuer, Privatsteuern | nicht übernommen | – |
| Umsatzsteuer-Vorauszahlungen | nicht übernommen, dienen als abgegebene Zahllast | – |

Reverse Charge ändert die Zahllast nicht (Steuer und Vorsteuer heben sich auf); ob ein Anbieter in der EU oder im
Drittland sitzt, ändert nur die Kennzahl. Der Hinweis am Beleg zeigt, wo das Werkzeug geschätzt hat.

Belege, deren sevDesk-Datum über ein Jahr vor der Zahlung liegt (z. B. STRATO-Rechnungen mit Datum 2016), behalten
das Datum aus sevDesk, weil sevDesk sie so angemeldet hat, und tragen einen Hinweis.

### Was beim Abgleich auffallen kann

Weichen einzelne Monate um kleine Beträge nach unten ab, sind das meist Rechnungen, die in sevDesk erst nach der
Abgabe erfasst wurden (z. B. Monatsrechnungen vom Monatsende): finanz-O-mat zählt ihre Vorsteuer im Monat der
Rechnung, in der abgegebenen Voranmeldung fehlt sie. Das gleicht die Jahreserklärung aus.

## Weg B: über die API

Das Werkzeug holt alles aus sevDesk, legt die Belegdateien in Paperless ab und übernimmt die Buchungen als geprüfte
Belege in finanz-O-mat. Es arbeitet in drei Schritten:

| Schritt | Befehl | Was passiert |
|---|---|---|
| 1 | `sevdesk sichern` | Alle Daten (JSON) und alle Beleg-, Rechnungs- und Gutschrift-PDFs in einen Ordner. Nur lesend. |
| 2 | `sevdesk importieren --trocken` | Probelauf: zeigt, was übernommen würde, und die Zahllast je Monat. Schreibt nichts. |
| 2 | `sevdesk importieren` | Dateien nach Paperless, Buchungen nach finanz-O-mat. Wiederholbar. |
| 3 | `sevdesk abgleich` | Zahllast je Monat gegen die abgegebenen Voranmeldungen. |

Schritt 2 und 3 brauchen sevDesk nicht mehr, nur den Sicherungsordner.

### Vor dem Start

- **API-Token** aus sevDesk: Einstellungen > Benutzer > API-Token. Er wird nur für Schritt 1 gebraucht und nur als
  Umgebungsvariable `SEVDESK_TOKEN` übergeben (nicht auf der Kommandozeile, damit er nicht in der Shell-History landet).
- **Für die Steuerberaterin** zusätzlich in sevDesk selbst den DATEV-Export (mit Belegbildern) für die offenen
  Jahre ziehen. Den liest finanz-O-mat nicht, er ist ihr gewohntes Format.
- **Paperless** muss in finanz-O-mat eingerichtet sein (Einstellungen oder `.env`). Die Tags „Eingangsrechnung“,
  „Ausgangsrechnung“ und „sevDesk“ legt das Werkzeug an, wenn es sie nicht gibt; Korrespondenten ebenso.
- sevDesk erst kündigen, wenn Schritt 3 passt und die Sicherung zusätzlich auf der Nextcloud liegt. Der Ordner aus
  Schritt 1 ist dein Archiv für die Aufbewahrungsfrist.

### Im Proxmox-Container

Als root in der Container-Shell (`pct enter <CTID>`):

```sh
set -a; . /etc/finanz-o-mat/finanz-o-mat.env; set +a      # DATENBANK, Paperless usw.
read -rs SEVDESK_TOKEN && export SEVDESK_TOKEN              # Token einfügen, Enter
ustva() { runuser -p -u finanzomat -- /opt/finanz-o-mat/.venv/bin/python -m ustva "$@"; }
SICHERUNG=/var/lib/finanz-o-mat/sevdesk
```

#### 1. Sichern

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

#### 2. Probelauf, dann übernehmen

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

#### 3. Abgleich mit den abgegebenen Voranmeldungen

Lege eine CSV mit der abgegebenen Zahllast (Kz 83) je Monat (oder Quartal: `2023-Q1`) an, z. B. aus Mein ELSTER (Übermittelte Formulare) oder
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

### Wie die Steuerregeln abgebildet werden

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

## Was nicht übernommen wird (beide Wege)

- **Entwürfe**, Vorlagen für wiederkehrende Belege und Rechnungen, Mahnungen.
- **Gutschriften** (CreditNote): gesichert, aber nicht als Beleg angelegt; die Ausgabe listet sie zum Nachtragen.
- **Bankumsätze**: gesichert in `CheckAccountTransaction.json`, aber nicht als Kontoumsätze angelegt. Für den
  Kontoabgleich in der App die N26-CSV der gewünschten Monate importieren.
- **Teilzahlungen**: Es zählt das Zahlungsdatum aus sevDesk; solche Rechnungen tragen einen Hinweis.

## Mögliche Abweichungen beim Abgleich (beide Wege)

- finanz-O-mat zieht Vorsteuer im Monat der Rechnung ab, Umsätze bei Ist-Versteuerung im Monat der Zahlung. Hat
  sevDesk die Vorsteuer nach Zahlungsdatum gebucht, verschiebt sich ein Betrag in den Nachbarmonat; die Summe über
  die Monate bleibt gleich.
- Bemessungsgrundlagen werden wie in ELSTER auf volle Euro abgerundet; das macht Cent-Differenzen.
- Belege, die in sevDesk erst nach der Abgabe korrigiert wurden, zeigen die korrigierten Werte.
