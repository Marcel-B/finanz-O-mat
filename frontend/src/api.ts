// Typen und Aufrufe der JSON-API des Backends (ustva/web.py)
import { ref } from "vue";

// false, sobald das Backend „Bitte anmelden“ meldet (Sitzung abgelaufen oder abgemeldet)
export const angemeldet = ref<boolean | null>(null);

export type Art = "ausgang" | "eingang";

export interface Frist {
  zeitraum: string;
  zeitraum_text: string;
  faellig: string;
  faellig_text: string;
  tage_bis: number;
  abgegeben: string[]; // übersprungen, weil das ELSTER-Protokoll in Paperless liegt
}

export interface Status {
  frist: Frist;
  versteuerung: "ist" | "soll";
  dauerfristverlaengerung: boolean;
  bundesland: string;
  verzeichnis: string | null;
  paperless: string | null;
  anmeldung: boolean;
}

export interface Anmeldung {
  aktiv: boolean; // ohne Passwort ist die App offen
  angemeldet: boolean;
}

export type Herkunft = "app" | "umgebung" | "";

export interface Feld {
  wert: string;
  quelle: Herkunft;
}

export interface Einstellungen {
  paperless_url: Feld;
  paperless_token: { gesetzt: boolean; quelle: Herkunft };
  paperless_tags_ausgang: Feld;
  paperless_tags_eingang: Feld;
  paperless_tag_kontoauszug: Feld;
  paperless_tag_protokoll: Feld;
  eigene_ustid: Feld;
  bundesland: Feld;
  bundeslaender: { wert: string; name: string }[];
  anmeldung: { aktiv: boolean; benutzer: string; quelle: Herkunft; min_laenge: number };
}

export type EinstellungenEingabe = Partial<Record<
  "paperless_url" | "paperless_token" | "paperless_tags_ausgang" | "paperless_tags_eingang" | "eigene_ustid" |
  "bundesland" | "paperless_tag_kontoauszug" | "paperless_tag_protokoll", string>>;

export interface Steuerfall {
  wert: string;
  titel: string;
  art: Art | null;
  kennzahlen: string;
  sonderfall: boolean;
}

export interface Kennzahl {
  kz: string;
  id: string;
  titel: string;
  betrag: string;
  betrag_art: "bemessungsgrundlage" | "steuer";
  von_elster_berechnet: boolean;
  ist_steuerzeile: boolean;
}

export interface Hinweis {
  stufe: "warnung" | "info" | "sonderfall";
  text: string;
}

export interface Beleg {
  id: number;
  art: Art;
  steuerfall: string;
  rechnungsdatum: string | null;
  zahlungsdatum: string | null;
  netto: string;
  steuersatz: string;
  steuer: string | null;
  waehrung: string;
  kurs: string | null;
  partner: string;
  partner_ustid: string;
  rechnungsnummer: string;
  beschreibung: string;
  quelle: string;
  quelle_link: string;
  erechnung: string;
  pruefen: boolean;
  gedruckt?: string; // Zeitpunkt des letzten Drucks, leer = nie
  hinweise: string[];
  netto_eur?: string | null;
  steuer_eur?: string | null;
  brutto_eur?: string | null;
  steuer_auf_rechnung?: boolean;
  kurs_verwendet?: string | null;
  fehler?: string;
}

export type BelegEingabe = Omit<Beleg, "id" | "quelle" | "quelle_link" | "erechnung" | "gedruckt" | "hinweise" | "netto_eur" |
  "steuer_eur" | "brutto_eur" | "steuer_auf_rechnung" | "kurs_verwendet" | "fehler">;

export interface BelegKurz {
  id: number;
  partner: string;
  beschreibung: string;
  rechnungsnummer: string;
  rechnungsdatum: string | null;
  zahlbetrag: string;
  waehrung: string;
  zugeordnet?: string; // Buchungsdatum des Umsatzes, an dem der Beleg schon hängt
}

export interface Umsatz {
  id: number;
  buchungsdatum: string;
  betrag: string;
  partner: string;
  verwendungszweck: string;
  typ: string;
  original_betrag: string | null;
  original_waehrung: string;
  beleg_id: number | null;
  ignoriert: boolean;
  ignoriert_grund: string;
  beleg: BelegKurz | null;
  kosten_geldverkehr?: string | null;
}

export interface Bericht {
  zeitraum: string;
  versteuerung: string;
  kennzahlen: Kennzahl[];
  umsatzsteuer: string;
  vorsteuer: string;
  zahllast: string;
  vollstaendig: boolean;
  hinweise: Hinweis[];
  zm: { ustid: string; art: string; betrag: string }[];
  belege: Beleg[];
  umsaetze: Umsatz[];
}

export interface Kurs {
  waehrung: string;
  zeitraum: string;
  kurs: string;
  quelle: string;
}

export interface ImportErgebnis {
  neu: number;
  vorhanden: number;
  zugeordnet?: number;
  fehler: string[];
  doppelt?: string[];
}

export interface NeuEingelesen {
  aktualisiert: number;
  neu: number;
  fehler: string[];
}

export interface Monat {
  zeitraum: string;
  monat: string;
  einnahmen: string;
  ausgaben: string;
  ergebnis: string;
  zahllast: string;
  belege: number;
  fehlerhaft: number;
}

export interface Jahresuebersicht {
  jahr: number;
  versteuerung: string;
  monate: Monat[];
  summe: { einnahmen: string; ausgaben: string; ergebnis: string; zahllast: string };
  kunden: { name: string; betrag: string }[];
  lieferanten: { name: string; betrag: string }[];
  unvollstaendig: number;
}

export interface UpdateStatus {
  unterstuetzt: boolean;
  zustand?: "aktuell" | "neu" | "angefordert" | "laeuft" | "fehler" | "unbekannt";
  installiert?: string | null;
  installiert_datum?: string | null;
  neu?: string | null;
  aenderungen: string[];
  geprueft?: string | null;
  meldung?: string;
  automatisch?: boolean;
  pruefung_laeuft?: boolean;
}

export interface BackupStatus {
  unterstuetzt: boolean;
  zustand?: "aus" | "ok" | "fehler" | "laeuft" | "angefordert";
  meldung?: string;
  ziel?: string | null;
  letzte_sicherung?: string | null;
  letzter_versuch?: string | null;
  anzahl?: number | null;
  groesse?: number | null;
}

export interface Taetigkeit {
  id: number;
  datum: string;
  stunden: string;
  taetigkeit: string;
  auftrag: string;
  referenz: string;
  satz: string | null;
  rolle: string;
  ort: string;
  leistungsart: string;
  betrag?: string | null;
}

export type TaetigkeitEingabe = Omit<Taetigkeit, "id" | "satz" | "betrag"> & { satz?: string | null };

export interface TaetigkeitenMonat {
  eintraege: Taetigkeit[];
  stunden: string;
  betrag: string;
  fehlende_tage: string[]; // Werktage bis heute ohne Eintrag
  vorschlag: TaetigkeitEingabe; // Vorbelegung für den nächsten Eintrag
  texte: { taetigkeit: string; auftrag: string; referenz: string }[]; // frühere Tätigkeiten, neueste zuerst
}

export interface TaetigkeitVorlage {
  titel: string;
  name: string;
  vorname: string;
  kopf: string[];
  stundensatz: string | null;
  stunden: string;
  auftraege: { name: string; nummer: string }[];
  rollen: string[];
  orte: string[];
  leistungsarten: string[];
}

export interface TaetigkeitImport {
  eintraege: number;
  monate: string[];
  uebersprungen: string[];
  vorlage: string[];
}

export interface StundenzettelTag {
  datum: string;
  stunden: string;
}

export interface StundenzettelEinstellungen {
  name: string;
  stunden: string; // je Monat
  wochentag: number; // 0 = Montag
}

export interface StundenzettelMonat {
  zeitraum: string;
  name: string;
  tage: StundenzettelTag[];
  stunden: string;
}

export interface StundenzettelJahr {
  monate: StundenzettelMonat[]; // Januar bis zum letzten Monat, nichts in der Zukunft
  aktuell: string;
  vergangen: string;
  einstellungen: StundenzettelEinstellungen;
}

export interface StundenzettelVorschlag {
  tage: StundenzettelTag[];
  feiertage: { datum: string; name: string }[]; // Arbeitstage, die wegen eines Feiertags fehlen
}

export interface StundenzettelImport {
  tage: number;
  monate: string[];
  uebersprungen: string[];
  andere_jahre: string[];
  name: string;
}

export interface DruckBeleg {
  id: number;
  name: string;
  partner: string;
  art: Art;
  rechnungsdatum: string | null;
  gedruckt: string;
  grund: string; // warum nicht druckbar; leer = druckbar
}

// Öffnet die Belege des Monats als ein PDF in einem neuen Tab. Ein Formular statt fetch, damit der Browser das
// PDF selbst anzeigt; der Server vermerkt die enthaltenen Belege als gedruckt.
export function belegeDrucken(zeitraum: string, ids?: number[]) {
  const formular = document.createElement("form");
  formular.method = "post";
  formular.target = "_blank";
  formular.action = `/api/druck/${zeitraum}/belege.pdf${ids?.length ? `?ids=${ids.join(",")}` : ""}`;
  document.body.appendChild(formular);
  formular.submit();
  formular.remove();
}

export interface MailVorlage {
  an: string;
  betreff: string; // Platzhalter {monat} und {jahr}
  text: string;
}

export interface MonatsabschlussStand {
  zeitraum: string;
  monat: string; // „September 2026“
  umsaetze: number;
  letzte_buchung: string | null;
  ohne_beleg: number;
  belege: number;
  pruefen: number;
  fehlerhaft: number;
  kontoauszug: boolean | null; // null: ohne Paperless nicht prüfbar
  protokoll: boolean | null;
  paperless: boolean;
  paperless_fehler: string;
  stundenzettel: string; // eingetragene Stunden im Monat
  mail: MailVorlage;
}

export interface Ablage {
  zustand: "fertig" | "doppelt" | "wartet" | "fehler";
  dokument: string;
  fehler: string;
  titel: string;
  tag: string;
}

export type Rolle = "" | "kunde" | "lieferant" | "beides";

export interface KontaktEingabe {
  name: string;
  rolle: Rolle;
  kundennummer: string;
  ustid: string;
  strasse: string;
  plz: string;
  ort: string;
  land: string;
  email: string;
  telefon: string;
  ansprechpartner: string;
  notiz: string;
}

export interface Kontakt extends KontaktEingabe {
  id: number;
  quellen: string[];
  aliase: string[];
  ausgeblendet: boolean;
  belege: number;
  ausgang: number;
  eingang: number;
  letzter_beleg: string | null;
}

export interface SevdeskVergleich {
  zeitraum: string;
  zahllast: string;
  erwartet: string | null;
  differenz: string | null;
  passt: boolean;
}

export interface SevdeskErgebnis {
  trocken: boolean;
  neu: number;
  belege: number;
  vorhanden: number;
  ausserhalb: number;
  hochgeladen: number;
  wartet: number;
  kontakte: number;
  uebersprungen: { grund: string; anzahl: number }[];
  doppelt: string[];
  pruefen: string[];
  ohne_datei: string[];
  fehler: string[];
  zahlungen_ohne_zeitraum: string[];
  vergleich: SevdeskVergleich[];
}

export interface SevdeskStatus {
  csv: string[];
  dateien: number;
  paperless: boolean;
  laeuft: boolean;
  meldung: string;
  ergebnis: SevdeskErgebnis | null;
  fehler: string;
  beendet: string;
}

async function anfrage<T>(pfad: string, optionen: RequestInit = {}): Promise<T> {
  const antwort = await fetch(pfad, {
    ...optionen,
    headers: { "Content-Type": "application/json", ...(optionen.headers ?? {}) },
  });
  if (antwort.status === 401 && pfad !== "/api/anmelden") angemeldet.value = false;
  if (!antwort.ok) {
    let text = `Fehler ${antwort.status}`;
    try {
      const daten = await antwort.json();
      if (typeof daten.detail === "string") text = daten.detail;
      else if (Array.isArray(daten.detail)) {
        text = daten.detail.map((d: { loc?: string[]; msg: string }) => `${d.loc?.at(-1)}: ${d.msg}`).join("; ");
      }
    } catch {
      /* keine JSON-Antwort */
    }
    throw new Error(text);
  }
  return (antwort.status === 204 ? null : await antwort.json()) as T;
}

const post = <T>(pfad: string, daten?: unknown) =>
  anfrage<T>(pfad, { method: "POST", body: daten === undefined ? undefined : JSON.stringify(daten) });

export const api = {
  anmeldung: () => anfrage<Anmeldung>("/api/anmeldung"),
  anmelden: (benutzer: string, passwort: string) => post<Anmeldung>("/api/anmelden", { benutzer, passwort }),
  abmelden: () => post<null>("/api/abmelden"),
  status: () => anfrage<Status>("/api/status"),
  steuerfaelle: () => anfrage<Steuerfall[]>("/api/steuerfaelle"),
  bericht: (zeitraum: string) => anfrage<Bericht>(`/api/bericht/${zeitraum}`),
  offen: () => anfrage<Beleg[]>("/api/offen"),
  beleg: (id: number) => anfrage<Beleg>(`/api/belege/${id}`),
  belegAnlegen: (b: BelegEingabe) => post<Beleg>("/api/belege", b),
  belegAendern: (id: number, b: BelegEingabe) =>
    anfrage<Beleg>(`/api/belege/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  belegGeprueft: (id: number) => post<Beleg>(`/api/belege/${id}/geprueft`),
  belegLoeschen: (id: number) => anfrage<null>(`/api/belege/${id}`, { method: "DELETE" }),
  belegeNeuEinlesen: (ids?: number[]) => post<NeuEingelesen>("/api/belege/neu-einlesen", { ids: ids ?? null }),
  einlesen: (zeitraum: string) => post<Record<string, ImportErgebnis>>("/api/import", { quelle: "alle", zeitraum }),
  bankImport: (csv: string) =>
    anfrage<ImportErgebnis>("/api/import/bank", { method: "POST", body: csv, headers: { "Content-Type": "text/csv" } }),
  kandidaten: (umsatzId: number, suche = "") =>
    anfrage<BelegKurz[]>(`/api/umsaetze/${umsatzId}/kandidaten?suche=${encodeURIComponent(suche)}`),
  zuordnen: (umsatzId: number, belegId: number | null) =>
    post<Umsatz>(`/api/umsaetze/${umsatzId}/zuordnen`, { beleg_id: belegId }),
  ignorieren: (umsatzId: number, daten: { ignoriert?: boolean; grund?: string; regel?: boolean }) =>
    post<Umsatz>(`/api/umsaetze/${umsatzId}/ignorieren`, daten),
  kontakte: () => anfrage<Kontakt[]>("/api/kontakte"),
  kontaktAnlegen: (k: KontaktEingabe) => post<Kontakt>("/api/kontakte", k),
  kontaktAendern: (id: number, k: KontaktEingabe) =>
    anfrage<Kontakt>(`/api/kontakte/${id}`, { method: "PUT", body: JSON.stringify(k) }),
  kontaktAusblenden: (id: number) => anfrage<null>(`/api/kontakte/${id}`, { method: "DELETE" }),
  kontakteZusammenfuehren: (ziel: number, mit: number) =>
    post<Kontakt>(`/api/kontakte/${ziel}/zusammenfuehren`, { mit }),
  sevdesk: () => anfrage<SevdeskStatus>("/api/sevdesk"),
  sevdeskLeeren: () => anfrage<null>("/api/sevdesk/dateien", { method: "DELETE" }),
  sevdeskHochladen: (name: string, datei: Blob) =>
    anfrage<{ abgelegt: number }>(`/api/sevdesk/dateien?name=${encodeURIComponent(name)}`, {
      method: "PUT",
      body: datei,
      headers: { "Content-Type": "application/octet-stream" },
    }),
  sevdeskProbelauf: (o: { von?: string | null; bis?: string | null }) => post<SevdeskErgebnis>("/api/sevdesk/probelauf", o),
  sevdeskImportieren: (o: { von?: string | null; bis?: string | null }) =>
    post<SevdeskStatus>("/api/sevdesk/importieren", o),
  kurse: () => anfrage<Kurs[]>("/api/kurse"),
  kursSpeichern: (k: { waehrung: string; zeitraum: string; kurs: string; quelle: string }) =>
    anfrage<Kurs[]>("/api/kurse", { method: "PUT", body: JSON.stringify(k) }),
  kursLoeschen: (waehrung: string, zeitraum: string) =>
    anfrage<null>(`/api/kurse/${waehrung}/${zeitraum}`, { method: "DELETE" }),
  einstellungen: () => anfrage<Einstellungen>("/api/einstellungen"),
  einstellungenSpeichern: (e: EinstellungenEingabe) =>
    anfrage<Einstellungen>("/api/einstellungen", { method: "PUT", body: JSON.stringify(e) }),
  paperlessTesten: (e: { paperless_url?: string; paperless_token?: string }) =>
    post<{ text: string }>("/api/einstellungen/paperless-test", e),
  passwortAendern: (p: { aktuell: string; benutzer: string; neu: string }) =>
    post<Einstellungen["anmeldung"]>("/api/passwort", p),
  uebersicht: (jahr: number) => anfrage<Jahresuebersicht>(`/api/uebersicht/${jahr}`),
  update: () => anfrage<UpdateStatus>("/api/update"),
  updateStarten: () => post<UpdateStatus>("/api/update"),
  updatePruefen: () => post<UpdateStatus>("/api/update/pruefen"),
  taetigkeiten: (zeitraum: string) => anfrage<TaetigkeitenMonat>(`/api/taetigkeiten?zeitraum=${zeitraum}`),
  taetigkeitAnlegen: (t: TaetigkeitEingabe) => post<Taetigkeit>("/api/taetigkeiten", t),
  taetigkeitAendern: (id: number, t: TaetigkeitEingabe) =>
    anfrage<Taetigkeit>(`/api/taetigkeiten/${id}`, { method: "PUT", body: JSON.stringify(t) }),
  taetigkeitLoeschen: (id: number) => anfrage<null>(`/api/taetigkeiten/${id}`, { method: "DELETE" }),
  taetigkeitVorlage: () => anfrage<TaetigkeitVorlage>("/api/taetigkeiten/vorlage"),
  taetigkeitVorlageSpeichern: (v: TaetigkeitVorlage) =>
    anfrage<TaetigkeitVorlage>("/api/taetigkeiten/vorlage", { method: "PUT", body: JSON.stringify(v) }),
  taetigkeitImport: (datei: Blob) =>
    anfrage<TaetigkeitImport>("/api/taetigkeiten/import", {
      method: "POST",
      body: datei,
      headers: { "Content-Type": "application/octet-stream" },
    }),
  stundenzettel: (jahr: number) => anfrage<StundenzettelJahr>(`/api/stundenzettel/${jahr}`),
  stundenzettelVorschlag: (zeitraum: string) =>
    anfrage<StundenzettelVorschlag>(`/api/stundenzettel/vorschlag/${zeitraum}`),
  stundenzettelEintragen: (zeitraum: string, tage: StundenzettelTag[]) =>
    anfrage<StundenzettelMonat[]>(`/api/stundenzettel/monat/${zeitraum}`, { method: "PUT", body: JSON.stringify({ tage }) }),
  stundenzettelEinstellungen: () => anfrage<StundenzettelEinstellungen>("/api/stundenzettel/einstellungen"),
  stundenzettelEinstellungenSpeichern: (e: StundenzettelEinstellungen) =>
    anfrage<StundenzettelEinstellungen>("/api/stundenzettel/einstellungen", { method: "PUT", body: JSON.stringify(e) }),
  stundenzettelImport: (jahr: number, datei: Blob) =>
    anfrage<StundenzettelImport>(`/api/stundenzettel/import/${jahr}`, {
      method: "POST",
      body: datei,
      headers: { "Content-Type": "application/octet-stream" },
    }),
  druckListe: (zeitraum: string) => anfrage<DruckBeleg[]>(`/api/druck/${zeitraum}`),
  monatsabschluss: (zeitraum: string) => anfrage<MonatsabschlussStand>(`/api/monatsabschluss/${zeitraum}`),
  monatsabschlussAblegen: (zeitraum: string, art: "kontoauszug" | "protokoll", datei: File) =>
    anfrage<Ablage>(`/api/monatsabschluss/${zeitraum}/ablegen/${art}?name=${encodeURIComponent(datei.name)}`, {
      method: "POST",
      body: datei,
      headers: { "Content-Type": "application/pdf" },
    }),
  mailVorlageSpeichern: (v: MailVorlage) =>
    anfrage<MailVorlage>("/api/monatsabschluss/mail", { method: "PUT", body: JSON.stringify(v) }),
  backup: () => anfrage<BackupStatus>("/api/backup"),
  backupStarten: () => post<BackupStatus>("/api/backup"),
};
