const euro2 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const euro0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

export const betrag = (wert: string | number | null | undefined, ganz = false): string =>
  wert == null || wert === "" ? "–" : (ganz ? euro0 : euro2).format(Number(wert));

export const datum = (iso: string | null | undefined): string => {
  if (!iso) return "–";
  const [j, m, t] = iso.split("-");
  return `${t}.${m}.${j}`;
};

// Datum <-> ISO ohne Zeitzonenverschiebung
export const alsIso = (d: Date | null | undefined): string | null =>
  d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : null;

export const ausIso = (iso: string | null | undefined): Date | null => {
  if (!iso) return null;
  const [j, m, t] = iso.split("-").map(Number);
  return new Date(j, m - 1, t ?? 1);
};

// So wie ELSTER es erwartet: Bemessungsgrundlagen in vollen Euro, Steuerbeträge mit Cent
export const elsterWert = (k: { betrag: string; betrag_art: string }): string => {
  const wert = Number(k.betrag);
  return k.betrag_art === "bemessungsgrundlage" ? String(Math.trunc(wert)) : wert.toFixed(2).replace(".", ",");
};

// "2026-10-11T13:05" -> "11.10.2026, 13:05 Uhr"
export const zeitpunkt = (iso: string | null | undefined): string =>
  iso ? `${datum(iso.slice(0, 10))}${iso.length > 10 ? `, ${iso.slice(11, 16)} Uhr` : ""}` : "–";

export const zeitraumVon = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;

// Volltextsuche in einer Liste: Jedes eingegebene Wort muss irgendwo vorkommen. Beträge findet man
// auch in deutscher Schreibweise ("62,89", "1.234,56"), Daten auch als TT.MM.JJJJ.
export function suchtext(...werte: unknown[]): string {
  const teile: string[] = [];
  for (const wert of werte.flat()) {
    if (wert == null || wert === "") continue;
    const s = String(wert);
    teile.push(s);
    if (/^-?\d+(\.\d+)?$/.test(s)) teile.push(betrag(s), s.replace(".", ","));
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) teile.push(datum(s));
  }
  return teile.join(" ").toLowerCase();
}

export const passtZurSuche = (text: string, suche: string): boolean =>
  suche.toLowerCase().split(/\s+/).filter(Boolean).every((wort) => text.includes(wort));
