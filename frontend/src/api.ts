// Typen und Aufrufe der JSON-API des Backends (ustva/web.py)

export type Art = "ausgang" | "eingang";

export interface Frist {
  zeitraum: string;
  zeitraum_text: string;
  faellig: string;
  faellig_text: string;
  tage_bis: number;
}

export interface Status {
  frist: Frist;
  versteuerung: "ist" | "soll";
  dauerfristverlaengerung: boolean;
  bundesland: string;
  verzeichnis: string | null;
  paperless: string | null;
}

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
  hinweise: string[];
  netto_eur?: string | null;
  steuer_eur?: string | null;
  kurs_verwendet?: string | null;
  fehler?: string;
}

export type BelegEingabe = Omit<Beleg, "id" | "quelle" | "quelle_link" | "erechnung" | "hinweise" | "netto_eur" | "steuer_eur" |
  "kurs_verwendet" | "fehler">;

export interface BelegKurz {
  id: number;
  partner: string;
  beschreibung: string;
  rechnungsnummer: string;
  rechnungsdatum: string | null;
  zahlbetrag: string;
  waehrung: string;
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

async function anfrage<T>(pfad: string, optionen: RequestInit = {}): Promise<T> {
  const antwort = await fetch(pfad, {
    ...optionen,
    headers: { "Content-Type": "application/json", ...(optionen.headers ?? {}) },
  });
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
  status: () => anfrage<Status>("/api/status"),
  steuerfaelle: () => anfrage<Steuerfall[]>("/api/steuerfaelle"),
  bericht: (zeitraum: string) => anfrage<Bericht>(`/api/bericht/${zeitraum}`),
  offen: () => anfrage<Beleg[]>("/api/offen"),
  belegAnlegen: (b: BelegEingabe) => post<Beleg>("/api/belege", b),
  belegAendern: (id: number, b: BelegEingabe) =>
    anfrage<Beleg>(`/api/belege/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  belegGeprueft: (id: number) => post<Beleg>(`/api/belege/${id}/geprueft`),
  belegLoeschen: (id: number) => anfrage<null>(`/api/belege/${id}`, { method: "DELETE" }),
  einlesen: (zeitraum: string) => post<Record<string, ImportErgebnis>>("/api/import", { quelle: "alle", zeitraum }),
  bankImport: (csv: string) =>
    anfrage<ImportErgebnis>("/api/import/bank", { method: "POST", body: csv, headers: { "Content-Type": "text/csv" } }),
  kandidaten: (umsatzId: number) => anfrage<BelegKurz[]>(`/api/umsaetze/${umsatzId}/kandidaten`),
  zuordnen: (umsatzId: number, belegId: number | null) =>
    post<Umsatz>(`/api/umsaetze/${umsatzId}/zuordnen`, { beleg_id: belegId }),
  ignorieren: (umsatzId: number, daten: { ignoriert?: boolean; grund?: string; regel?: boolean }) =>
    post<Umsatz>(`/api/umsaetze/${umsatzId}/ignorieren`, daten),
  kurse: () => anfrage<Kurs[]>("/api/kurse"),
  kursSpeichern: (k: { waehrung: string; zeitraum: string; kurs: string; quelle: string }) =>
    anfrage<Kurs[]>("/api/kurse", { method: "PUT", body: JSON.stringify(k) }),
  kursLoeschen: (waehrung: string, zeitraum: string) =>
    anfrage<null>(`/api/kurse/${waehrung}/${zeitraum}`, { method: "DELETE" }),
  uebersicht: (jahr: number) => anfrage<Jahresuebersicht>(`/api/uebersicht/${jahr}`),
};
