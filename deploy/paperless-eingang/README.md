# Rechnungen automatisch nach Paperless-ngx holen

`paperless-eingang.sh` nimmt den Handgriff zwischen Postfach und Paperless ab. Es richtet zwei Wege ein:

1. **Mail-Abruf:** Paperless meldet sich selbst per IMAP am Postfach an (z. B. Strato) und holt alle 10 Minuten die
   PDF-Anhänge aus den Ordnern, in die Rechnungen einsortiert werden. Die Dokumente bekommen die Tags
   `Eingangsrechnung` und `E-Mail`; Korrespondent und weitere Tags erkennt Paperless selbst (Zuordnung
   „Automatisch“, lernt aus den schon getaggten Belegen). Damit sieht finanz-O-mat sie beim nächsten
   „Rechnungen einlesen“.
2. **Eingangsordner:** Was nicht per Mail kommt (Downloads aus Kundenportalen, Scans), landet wie bisher per Dropzone
   in einem Ordner der Nextcloud, z. B. `__unverarbeitet__`. Alle 2 Minuten holt der Paperless-Container die Dateien
   dort ab, übergibt sie an Paperless und entfernt sie aus der Nextcloud (und damit über den Nextcloud-Client auch
   vom eigenen Rechner). Unterordner werden zu Tags: `__unverarbeitet__/Kontoauszug/2026-09.pdf` bekommt den Tag
   `Kontoauszug`.

Beide Wege lassen das Postfach so, wie es ist: Mails werden weder verschoben noch gelöscht.

## Einrichten

Vorher bereitlegen:

- **Paperless-API-Token:** in Paperless oben rechts > „Mein Profil“ > API-Auth-Token (dasselbe wie in finanz-O-mat
  geht auch).
- **Passwort des Postfachs.** Bei Strato ist das das Passwort der E-Mail-Adresse (nicht das des Kundenlogins);
  Server `imap.strato.de`, Port 993 mit SSL, Benutzername ist die volle Adresse.
- **Nextcloud-App-Passwort**, falls `paperless-backup` noch keins hinterlegt hat (Nextcloud > Persönliche
  Einstellungen > Sicherheit > „Neues App-Passwort erstellen“). Ist das Backup schon eingerichtet, übernimmt das
  Skript dessen Zugang.

Dann als root in der Shell von Paperless (beim Helper-Script: Proxmox > Container > Konsole, oder `pct enter <CTID>`
auf dem Host):

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/finanz-O-mat/main/deploy/paperless-eingang/paperless-eingang.sh)" -- einrichten
```

### Mail-Abruf

Das Skript meldet sich am Postfach an und zeigt alle Ordner mit Nummern. Dort die Ordner wählen, in die Rechnungen
sortiert werden (z. B. `4,7,9`). Für jeden legt es in Paperless eine Regel `Rechnungen: <Ordner>` an.

Gegen falsch einsortierte Mails kann Paperless zusätzlich den **Posteingang** durchsuchen, aber nur nach
PDF-Anhängen, deren Name nach Rechnung aussieht (Vorgabe: `*rechnung*.pdf`, `*invoice*.pdf`, `*receipt*.pdf`,
`*quittung*.pdf`). Das ergibt je Muster eine Regel `Posteingang: <Muster>`. Rechnungen, die in einem anderen
falschen Ordner landen, findet das nicht; die Sortierregeln im Postfach bleiben also wichtig.

Woran erkennt Paperless, was es schon geholt hat? Es merkt sich jede verarbeitete Mail und setzt zusätzlich ein
unsichtbares IMAP-Schlüsselwort `paperless`. Gelesen, ungelesen, verschoben: das eigene Mailprogramm merkt davon
nichts. Kann der Server keine Schlüsselwörter speichern, markiert Paperless abgeholte Mails stattdessen mit Stern;
das Skript sagt dann Bescheid.

Beim ersten Abruf holt Paperless Mails der letzten 30 Tage (einstellbar). Damit Rechnungen, die schon von Hand in
Paperless liegen, nicht doppelt ankommen, schaltet das Skript auf Nachfrage `PAPERLESS_CONSUMER_DELETE_DUPLICATES`
ein: Paperless verwirft dann Dateien, die es byte-gleich schon gibt (ab Paperless 3.0 legt es sie sonst doppelt an).
Dafür startet das Skript die Dienste `paperless-consumer` und `paperless-task-queue` einmal neu.

Paperless vermerkt eine Mail erst als erledigt, wenn ihr PDF fertig verarbeitet ist (Texterkennung). Ist die
Warteschlange beim ersten Abruf lang, kann der nächste Lauf zehn Minuten später dieselbe Mail noch einmal holen; das
Skript stößt deshalb keinen Sofort-Abruf mehr an. Gleiche Dateien verwirft Paperless mit
`PAPERLESS_CONSUMER_DELETE_DUPLICATES`, nur wenn beide Kopien gleichzeitig verarbeitet werden, rutschen sie durch.
Solche Doppel räumt `finanz-o-mat paperless duplikate` auf.

Erneut `paperless-eingang mail` ausführen, um Ordner hinzuzufügen oder abzuwählen (abgewählte Regeln werden
abgeschaltet, nicht gelöscht). Alles lässt sich auch in Paperless unter „E-Mail“ ansehen und ändern; was Paperless
mit jeder Mail gemacht hat, steht dort unter „Verarbeitete Mails“.

### Eingangsordner

Gefragt wird nach dem Ordner in der Nextcloud, so wie er dort im Browser heißt, z. B. `Finanzen/__unverarbeitet__`.
Bei „Unterordner als Tags“ legt das Skript darin `Eingangsrechnung`, `Ausgangsrechnung`, `Kontoauszug` und
`UStVA-Protokoll` an (die Tags, die finanz-O-mat liest) und schaltet in `paperless.conf`
`PAPERLESS_CONSUMER_RECURSIVE` und `PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS` ein (die Datei vor der ersten Änderung bleibt
als `paperless.conf.vor-eingang` liegen). Weitere Unterordner einfach in der Nextcloud anlegen; den Tag legt Paperless
beim ersten Dokument an. Dateien direkt in `__unverarbeitet__` bekommen nur den Posteingangs-Tag.

Liegen beim Einrichten schon Dateien im Ordner, sagt das Skript wie viele; der erste Lauf übergibt alle.

Dropzone kann so bleiben, wie es ist: Es legt Dateien in den lokalen Nextcloud-Ordner, der Nextcloud-Client lädt
sie hoch, und spätestens 2 Minuten danach (frühestens wenn die Datei eine Minute unverändert liegt) sind sie in
Paperless. Versteckte Dateien, Office-Sperrdateien (`~$…`) und `*.part`/`*.tmp` bleiben liegen.

## Was wo liegt

| Pfad | Inhalt |
|---|---|
| `/usr/local/sbin/paperless-eingang` | das Skript |
| `/usr/local/lib/paperless-eingang/mailabruf.py` | Einrichtung des Mail-Abrufs (spricht mit der Paperless-API) |
| `/etc/paperless-eingang/paperless-eingang.env` | Konfiguration des Eingangsordners |
| `/etc/paperless-eingang/rclone.conf` | Nextcloud-Zugang (Passwort mit `rclone obscure` verschleiert) |
| `/etc/systemd/system/paperless-eingang.{service,timer}` | Abholen alle 2 Minuten |
| `/var/lib/paperless-eingang/abgeholt` | Zwischenablage während eines Laufs, normalerweise leer |
| Paperless > E-Mail | Mailkonto „Strato“ und die Regeln; Zugangsdaten des Postfachs liegen nur dort |

## Im Alltag

```sh
paperless-eingang status                 # nächster Lauf, wartende Dateien, letzte Übergaben
paperless-eingang abholen                # sofort abholen
journalctl -u paperless-eingang -n 50    # Protokoll des Eingangsordners
paperless-eingang mail                   # Rechnungsordner ändern, neues Passwort eintragen
paperless-eingang ordner                 # anderen Nextcloud-Ordner wählen
systemctl disable --now paperless-eingang.timer   # Eingangsordner abschalten
```

Fehler beim Mail-Abruf zeigt Paperless unter „E-Mail“ beim Konto und unter Einstellungen > Protokolle (`mail`).

**Falsch erwischt?** Ein PDF, das keine Rechnung ist (AGB, Newsletter), in Paperless löschen. In finanz-O-mat
erscheinen neue Belege ohnehin als ungeprüft, solche Ausreißer fallen dort auf.

**Docker:** Statt des Helper-Script-LXC geht auch ein Docker-Host. Das Skript nimmt den laufenden
`paperless-ngx`-Container und dessen unter `/usr/src/paperless/consume` eingebundenen Ordner. Die Einstellungen
(`PAPERLESS_CONSUMER_RECURSIVE`, `PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS`, `PAPERLESS_CONSUMER_DELETE_DUPLICATES`)
trägt es in die `docker-compose.env` im Compose-Verzeichnis des Containers ein (vorher gesichert als
`docker-compose.env.vor-eingang`) und erstellt den Container mit `docker compose up -d` neu. Gibt es keine
`docker-compose.env`, nennt es die Werte, die noch fehlen. Die Paperless-Adresse findet es über `docker port`.
