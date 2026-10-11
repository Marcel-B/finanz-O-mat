<script setup lang="ts">
import Button from "primevue/button";
import DatePicker from "primevue/datepicker";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Step from "primevue/step";
import StepList from "primevue/steplist";
import StepPanel from "primevue/steppanel";
import StepPanels from "primevue/steppanels";
import Stepper from "primevue/stepper";
import Textarea from "primevue/textarea";
import { useToast } from "primevue/usetoast";
import { computed, reactive, ref, watch } from "vue";

import { api, type Bericht, type Kennzahl, type MailVorlage, type MonatsabschlussStand, type Status } from "@/api";
import { ausIso, betrag, datum, elsterWert, zeitraumVon } from "@/format";

import KarteBox from "./KarteBox.vue";

// Geführter Monatsabschluss: ein Angebot, die übrigen Reiter funktionieren wie bisher. Was sich prüfen
// lässt (Umsätze, Paperless, Stundenzettel), kommt vom Server; die Schritte zeigen es als erledigt an,
// egal ob es hier oder in den anderen Reitern erledigt wurde.
const props = defineProps<{ status: Status; aktiv: boolean }>();
const emit = defineEmits<{ oeffnen: [reiter: string, zeitraum: string]; geaendert: [] }>();
const toast = useToast();

const N26 = "https://app.n26.com/";
const ELSTER = "https://www.elster.de/eportal/login";
const MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
  "November", "Dezember"];
// Wo die Kennzahlen im ELSTER-Formular „Umsatzsteuer-Voranmeldung“ stehen (Abschnitte wie im Vordruck USt 1 A)
const ABSCHNITTE: { titel: string; kz: string[] }[] = [
  { titel: "Lieferungen und sonstige Leistungen › Steuerpflichtige Umsätze", kz: ["81", "86", "35", "36"] },
  { titel: "Lieferungen und sonstige Leistungen › Steuerfreie Umsätze mit Vorsteuerabzug", kz: ["41", "43"] },
  { titel: "Innergemeinschaftliche Erwerbe", kz: ["89", "93"] },
  { titel: "Ergänzende Angaben zu Umsätzen", kz: ["21", "45"] },
  { titel: "Leistungsempfänger als Steuerschuldner (§ 13b UStG)", kz: ["46", "47", "84", "85"] },
  { titel: "Abziehbare Vorsteuerbeträge", kz: ["66", "61", "67"] },
];
const SCHRITTE = ["Kontoauszug", "Belege", "Prüfen", "ELSTER", "Protokoll", "Steuerberatung", "Stundenzettel"];

// Gestartet ist pro Browser vermerkt, damit ein Neuladen mitten im Abschluss an derselben Stelle weitergeht
const SPEICHER = "finanzomat.monatsabschluss";
interface Gemerkt {
  zeitraum: string;
  schritt: number;
  gestartet: number;
  mail: boolean;
}
function gemerkt(): Gemerkt | null {
  try {
    const g = JSON.parse(localStorage.getItem(SPEICHER) ?? "null") as Gemerkt | null;
    return g && Date.now() - g.gestartet < 40 * 86400_000 ? g : null;
  } catch {
    return null;
  }
}
function merken() {
  try {
    if (laeuft.value) {
      const g: Gemerkt = { zeitraum: zeitraum.value, schritt: schritt.value, gestartet: gestartet, mail: mailVorbereitet.value };
      localStorage.setItem(SPEICHER, JSON.stringify(g));
    } else localStorage.removeItem(SPEICHER);
  } catch {
    /* ohne Speicher geht es auch */
  }
}

const alt = gemerkt();
const monat = ref<Date>(ausIso(`${alt?.zeitraum ?? props.status.frist.zeitraum}-01`) ?? new Date());
const zeitraum = computed(() => zeitraumVon(monat.value));
const monatsname = computed(() => MONATE[monat.value.getMonth()]);
const jahr = computed(() => monat.value.getFullYear());
const laeuft = ref(!!alt);
const schritt = ref(alt?.schritt ?? 1);
let gestartet = alt?.gestartet ?? Date.now();
const mailVorbereitet = ref(alt?.mail ?? false);

const stand = ref<MonatsabschlussStand | null>(null);
const bericht = ref<Bericht | null>(null);
const laedt = ref(false);
const beschaeftigt = reactive({ csv: false, kontoauszug: false, protokoll: false, belege: false, stunden: false, mail: false });
const vorlage = reactive<MailVorlage>({ an: "", betreff: "", text: "" });
const vorlageOffen = ref(false);

watch([zeitraum, schritt, laeuft, mailVorbereitet], merken);

function fehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

async function laden() {
  if (!laeuft.value) return;
  laedt.value = true;
  try {
    [stand.value, bericht.value] = await Promise.all([api.monatsabschluss(zeitraum.value), api.bericht(zeitraum.value)]);
    Object.assign(vorlage, stand.value.mail);
    vorlageOffen.value ||= !vorlage.an;
  } catch (e) {
    fehler(e);
  } finally {
    laedt.value = false;
  }
}

watch(zeitraum, () => {
  mailVorbereitet.value = false;
  laden();
});
// Beim Zurückkommen aus einem anderen Reiter den Stand neu holen
watch(() => props.aktiv, (aktiv) => aktiv && laden(), { immediate: true });

function starten() {
  laeuft.value = true;
  schritt.value = 1;
  gestartet = Date.now();
  mailVorbereitet.value = false;
  laden();
}

function beenden() {
  laeuft.value = false;
  stand.value = null;
  merken();
}

const erledigt = computed((): Record<number, boolean> => {
  const s = stand.value;
  if (!s) return {};
  return {
    1: s.umsaetze > 0 && s.kontoauszug !== false,
    2: s.belege > 0,
    3: s.umsaetze > 0 && s.ohne_beleg === 0 && s.pruefen === 0 && s.fehlerhaft === 0,
    4: s.protokoll === true,
    5: s.protokoll === true,
    6: mailVorbereitet.value,
    7: Number(s.stundenzettel) > 0,
  };
});

// 1. Kontoauszug
const csvWahl = ref<HTMLInputElement | null>(null);
const pdfWahl = ref<HTMLInputElement | null>(null);
const protokollWahl = ref<HTMLInputElement | null>(null);

function datei(event: Event): File | null {
  const eingabe = event.target as HTMLInputElement;
  const f = eingabe.files?.[0] ?? null;
  eingabe.value = "";
  return f;
}

async function csvHochladen(event: Event) {
  const f = datei(event);
  if (!f) return;
  beschaeftigt.csv = true;
  try {
    const e = await api.bankImport(await f.text());
    toast.add({
      severity: e.fehler.length ? "warn" : "success",
      summary: "Kontoumsätze eingelesen",
      detail: [`${e.neu} neu, ${e.vorhanden} schon vorhanden, ${e.zugeordnet ?? 0} Belegen zugeordnet`, ...e.fehler].join(" · "),
      life: 6000,
    });
    emit("geaendert");
    await laden();
  } catch (err) {
    fehler(err);
  } finally {
    beschaeftigt.csv = false;
  }
}

async function ablegen(event: Event, art: "kontoauszug" | "protokoll") {
  const f = datei(event);
  if (!f) return;
  beschaeftigt[art] = true;
  try {
    const a = await api.monatsabschlussAblegen(zeitraum.value, art, f);
    const texte = {
      fertig: [`„${a.titel}“ liegt in Paperless (Tag ${a.tag}).`, "success"],
      doppelt: [`Die Datei lag schon in Paperless; das Dokument hat jetzt den Tag ${a.tag}.`, "success"],
      wartet: ["Paperless verarbeitet die Datei noch. Gleich noch einmal aktualisieren.", "info"],
      fehler: [`Paperless meldet: ${a.fehler}`, "error"],
    } as const;
    const [text, stufe] = texte[a.zustand];
    toast.add({ severity: stufe, summary: art === "kontoauszug" ? "Kontoauszug" : "ELSTER-Protokoll", detail: text, life: 7000 });
    emit("geaendert");
    await laden();
  } catch (err) {
    fehler(err);
  } finally {
    beschaeftigt[art] = false;
  }
}

// 2. Belege
async function belegeEinlesen() {
  beschaeftigt.belege = true;
  try {
    const ergebnis = await api.einlesen(zeitraum.value);
    const namen: Record<string, string> = { verzeichnis: "Verzeichnis", paperless: "Paperless", konto: "Konto" };
    const teile: string[] = [];
    for (const [quelle, e] of Object.entries(ergebnis)) {
      if (quelle !== "keine") teile.push(`${namen[quelle]}: ${e.neu} neu, ${e.vorhanden} vorhanden`);
      for (const f of [...e.fehler, ...(e.doppelt ?? [])]) toast.add({ severity: "warn", summary: "Hinweis", detail: f, life: 8000 });
    }
    if (teile.length) toast.add({ severity: "success", summary: "Eingelesen", detail: teile.join(" · "), life: 5000 });
    emit("geaendert");
    await laden();
  } catch (e) {
    fehler(e);
  } finally {
    beschaeftigt.belege = false;
  }
}

// 4. ELSTER
const kennzahlen = computed(() => bericht.value?.kennzahlen.filter((k) => k.kz !== "83") ?? []);
const abschnitte = computed(() =>
  ABSCHNITTE.map((a) => ({ titel: a.titel, kennzahlen: kennzahlen.value.filter((k) => a.kz.includes(k.kz)) }))
    .filter((a) => a.kennzahlen.length),
);
const zahllast = computed(() => Number(bericht.value?.zahllast ?? 0));

async function kopieren(k: Kennzahl) {
  await navigator.clipboard?.writeText(elsterWert(k));
  toast.add({ severity: "info", summary: `Kz ${k.kz} kopiert`, detail: elsterWert(k), life: 1500 });
}

// 6. Steuerberatung
const ersetzen = (text: string) => text.replaceAll("{monat}", monatsname.value).replaceAll("{jahr}", String(jahr.value));
const mailLink = computed(() => {
  const betreff = encodeURIComponent(ersetzen(stand.value?.mail.betreff ?? ""));
  const text = encodeURIComponent(ersetzen(stand.value?.mail.text ?? "").replace(/\r?\n/g, "\r\n"));
  return `mailto:${stand.value?.mail.an.split(",").map((a) => encodeURIComponent(a.trim())).join(",") ?? ""}?subject=${betreff}&body=${text}`;
});
const vorlageGeaendert = computed(() =>
  stand.value != null && (["an", "betreff", "text"] as const).some((k) => vorlage[k] !== stand.value!.mail[k]),
);

async function vorlageSpeichern() {
  beschaeftigt.mail = true;
  try {
    const v = await api.mailVorlageSpeichern({ ...vorlage });
    if (stand.value) stand.value.mail = v;
    Object.assign(vorlage, v);
    vorlageOffen.value = false;
    toast.add({ severity: "success", summary: "Vorlage gespeichert", life: 2500 });
  } catch (e) {
    fehler(e);
  } finally {
    beschaeftigt.mail = false;
  }
}

// 7. Stundenzettel
async function stundenEintragen() {
  beschaeftigt.stunden = true;
  try {
    const v = await api.stundenzettelVorschlag(zeitraum.value);
    await api.stundenzettelEintragen(zeitraum.value, v.tage);
    const frei = v.feiertage.map((f) => `${datum(f.datum)} ${f.name}`).join(", ");
    toast.add({
      severity: "success",
      summary: "Stundenzettel eingetragen",
      detail: `${v.tage.length} Tage${frei ? `, ohne Feiertag ${frei}` : ""}`,
      life: 5000,
    });
    await laden();
  } catch (e) {
    fehler(e);
  } finally {
    beschaeftigt.stunden = false;
  }
}

const anzahl = (n: number, eins: string, mehr: string) => `${n} ${n === 1 ? eins : mehr}`;
const stunden = (wert: string | number) =>
  Number(wert).toLocaleString("de-DE", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
</script>

<template>
  <KarteBox v-if="!laeuft" titel="Monatsabschluss">
    <p class="mb-4 max-w-3xl text-muted-color">
      Führt Schritt für Schritt durch das Monatsende: Kontoauszug, Belege, Prüfen, Abgabe in ELSTER, Protokoll,
      Unterlagen an die Steuerberatung und Stundenzettel. Alles bleibt auch wie gewohnt in den anderen Reitern
      möglich; Schritte lassen sich überspringen.
    </p>
    <div class="flex flex-wrap items-end gap-2">
      <label class="flex flex-col gap-1 text-sm text-muted-color">
        Monat
        <DatePicker v-model="monat" view="month" date-format="MM yy" show-icon :manual-input="false" class="w-52" />
      </label>
      <Button label="Monatsabschluss starten" icon="pi pi-play" @click="starten" />
    </div>
  </KarteBox>

  <KarteBox v-else :titel="`Monatsabschluss ${monatsname} ${jahr}`">
    <template #aktionen>
      <Button icon="pi pi-refresh" severity="secondary" text rounded :loading="laedt" aria-label="Aktualisieren"
              v-tooltip.left="'Stand neu prüfen'" @click="laden" />
      <Button label="Beenden" icon="pi pi-times" severity="secondary" text @click="beenden" />
    </template>
    <Message v-if="stand?.paperless_fehler" severity="warn" class="mb-3">
      Paperless antwortet gerade nicht ({{ stand.paperless_fehler }}). Kontoauszug und Protokoll lassen sich deshalb nicht prüfen.
    </Message>

    <Stepper v-model:value="schritt" class="monatsabschluss">
      <StepList>
        <Step v-for="(titel, i) in SCHRITTE" :key="titel" :value="i + 1">
          {{ titel }}<i v-if="erledigt[i + 1]" class="pi pi-check-circle ml-1 text-green-500" aria-label="erledigt" />
        </Step>
      </StepList>
      <StepPanels class="!px-0">
        <!-- 1. Kontoauszug -->
        <StepPanel :value="1">
          <h3 class="mb-2 font-semibold">Kontoauszug von N26 holen</h3>
          <ol class="mb-4 list-decimal space-y-1 pl-5">
            <li>
              Im N26-Webbanking den Kontoauszug für {{ monatsname }} {{ jahr }} als <strong>PDF</strong> und die Umsätze
              des Monats als <strong>CSV</strong> herunterladen.
            </li>
            <li>Beide Dateien hier übernehmen: die CSV kommt in die Kontoumsätze, die PDF nach Paperless.</li>
          </ol>
          <div class="mb-4 flex flex-wrap gap-2">
            <a :href="N26" target="_blank" rel="noopener">
              <Button label="N26 öffnen" icon="pi pi-external-link" severity="secondary" />
            </a>
            <Button label="CSV einlesen" icon="pi pi-upload" :loading="beschaeftigt.csv" @click="csvWahl?.click()" />
            <Button
              label="PDF nach Paperless"
              icon="pi pi-cloud-upload"
              :loading="beschaeftigt.kontoauszug"
              :disabled="!status.paperless"
              v-tooltip.top="status.paperless ? `Ablage als „Kontoauszug ${monatsname} ${jahr}“` : 'Paperless ist nicht eingerichtet'"
              @click="pdfWahl?.click()"
            />
            <input ref="csvWahl" type="file" accept=".csv,text/csv" hidden @change="csvHochladen" />
            <input ref="pdfWahl" type="file" accept=".pdf,application/pdf" hidden @change="ablegen($event, 'kontoauszug')" />
          </div>
          <ul v-if="stand" class="status space-y-1 text-sm">
            <li>
              <i :class="stand.umsaetze ? 'pi pi-check-circle text-green-500' : 'pi pi-circle text-muted-color'" class="mr-2" />
              <template v-if="stand.umsaetze">
                {{ stand.umsaetze }} Kontoumsätze im {{ monatsname }}, letzte Buchung am {{ datum(stand.letzte_buchung) }}
              </template>
              <template v-else>Noch keine Kontoumsätze für {{ monatsname }}</template>
            </li>
            <li v-if="stand.kontoauszug !== null">
              <i :class="stand.kontoauszug ? 'pi pi-check-circle text-green-500' : 'pi pi-circle text-muted-color'" class="mr-2" />
              Kontoauszug in Paperless {{ stand.kontoauszug ? "gefunden" : "noch nicht gefunden" }}
            </li>
          </ul>
        </StepPanel>

        <!-- 2. Belege -->
        <StepPanel :value="2">
          <h3 class="mb-2 font-semibold">Belege importieren</h3>
          <p class="mb-4 text-muted-color">
            Holt neue Rechnungen aus Paperless{{ status.verzeichnis ? " und dem Rechnungsverzeichnis" : "" }} und ordnet sie
            den Kontoumsätzen zu. Fehlt eine Rechnung, erst in Paperless ablegen (Mail-Abruf oder Nextcloud-Ordner) und dann
            noch einmal einlesen.
          </p>
          <Button label="Rechnungen einlesen" icon="pi pi-download" class="mb-4" :loading="beschaeftigt.belege"
                  :disabled="!status.paperless && !status.verzeichnis" @click="belegeEinlesen" />
          <p v-if="stand" class="status text-sm">
            <i :class="stand.belege ? 'pi pi-check-circle text-green-500' : 'pi pi-circle text-muted-color'" class="mr-2" />
            {{ anzahl(stand.belege, "Beleg", "Belege") }} in der Voranmeldung {{ monatsname }}
          </p>
        </StepPanel>

        <!-- 3. Prüfen -->
        <StepPanel :value="3">
          <h3 class="mb-2 font-semibold">Daten sichten, korrigieren und verknüpfen</h3>
          <p class="mb-3 text-muted-color">
            In der Voranmeldung jeden Kontoumsatz einem Beleg zuordnen (oder als „kein Beleg nötig“ markieren) und
            ungeprüfte Belege kontrollieren.
          </p>
          <ul v-if="stand" class="status mb-3 space-y-1 text-sm">
            <li>
              <i :class="stand.ohne_beleg ? 'pi pi-exclamation-circle text-amber-500' : 'pi pi-check-circle text-green-500'" class="mr-2" />
              {{ stand.ohne_beleg ? anzahl(stand.ohne_beleg, "Kontoumsatz", "Kontoumsätze") + " ohne Beleg" : "Alle Kontoumsätze haben einen Beleg oder brauchen keinen" }}
            </li>
            <li>
              <i :class="stand.pruefen ? 'pi pi-exclamation-circle text-amber-500' : 'pi pi-check-circle text-green-500'" class="mr-2" />
              {{ stand.pruefen ? anzahl(stand.pruefen, "Beleg", "Belege") + " noch nicht geprüft" : "Alle Belege geprüft" }}
            </li>
            <li v-if="stand.fehlerhaft">
              <i class="pi pi-times-circle mr-2 text-red-500" />{{ anzahl(stand.fehlerhaft, "Beleg", "Belege") }} mit Fehler (z. B. Kurs fehlt)
            </li>
          </ul>
          <div v-if="bericht?.hinweise.length" class="mb-3 flex flex-col gap-2">
            <Message v-for="(h, i) in bericht.hinweise" :key="i" :severity="h.stufe === 'warnung' ? 'warn' : 'info'" size="small">
              {{ h.text }}
            </Message>
          </div>
          <Button label="In der Voranmeldung öffnen" icon="pi pi-arrow-right" icon-pos="right"
                  @click="emit('oeffnen', 'voranmeldung', zeitraum)" />
        </StepPanel>

        <!-- 4. ELSTER -->
        <StepPanel :value="4">
          <h3 class="mb-2 font-semibold">In Mein ELSTER eintragen</h3>
          <ol class="mb-4 list-decimal space-y-1 pl-5">
            <li>Bei Mein ELSTER anmelden.</li>
            <li>
              <strong>Formulare &amp; Leistungen › Alle Formulare › Umsatzsteuer › Umsatzsteuer-Voranmeldung {{ jahr }}</strong>
              öffnen.
            </li>
            <li>Unter <strong>Allgemeine Angaben</strong> Steuernummer und Zeitraum <strong>{{ monatsname }}</strong> wählen.</li>
            <li>Die Werte unten in die genannten Abschnitte eintragen; alle anderen Felder bleiben leer.</li>
            <li>
              Auf <strong>Prüfen</strong> gehen: ELSTER rechnet die Steuer selbst und sollte als Kz 83
              <strong>{{ betrag(bericht?.zahllast) }} €</strong> zeigen. Dann absenden und das Übertragungsprotokoll (PDF)
              herunterladen.
            </li>
          </ol>
          <a :href="ELSTER" target="_blank" rel="noopener">
            <Button label="Mein ELSTER öffnen" icon="pi pi-external-link" class="mb-4" />
          </a>
          <Message v-if="bericht && !bericht.vollstaendig" severity="error" size="small" class="mb-3">
            Die Werte sind unvollständig; erst Schritt 3 erledigen.
          </Message>
          <div v-if="bericht && kennzahlen.length === 0" class="text-muted-color">
            Keine Werte: alle Kennzahlen bleiben leer (Nullmeldung).
          </div>
          <div v-for="a in abschnitte" :key="a.titel" class="mb-3">
            <div class="mb-1 text-sm font-semibold text-muted-color">{{ a.titel }}</div>
            <table class="w-full text-left">
              <tr
                v-for="k in a.kennzahlen"
                :key="k.id"
                class="border-b border-surface-100 dark:border-surface-800"
                :class="k.ist_steuerzeile ? 'text-sm text-muted-color' : ''"
              >
                <td class="w-16 py-1.5 pr-2 font-bold">{{ k.ist_steuerzeile ? "" : `Kz ${k.kz}` }}</td>
                <td class="py-1.5 pr-2">{{ k.titel }}</td>
                <td class="zahl py-1.5 pr-2 text-right font-semibold whitespace-nowrap">
                  {{ betrag(k.betrag, k.betrag_art === "bemessungsgrundlage") }} €
                </td>
                <td class="w-28 py-1.5 text-right">
                  <span v-if="k.von_elster_berechnet" class="text-xs text-muted-color">rechnet ELSTER</span>
                  <Button v-else size="small" text icon="pi pi-copy" label="Kopieren" @click="kopieren(k)" />
                </td>
              </tr>
            </table>
          </div>
          <div
            v-if="bericht"
            class="mt-3 flex flex-wrap items-baseline justify-between gap-2 rounded-lg bg-surface-100 p-3 dark:bg-surface-800"
          >
            <div>
              <strong>Kz 83</strong> {{ zahllast < 0 ? "Überschuss (Erstattung)" : "Zahllast" }}
              <div class="text-sm text-muted-color">zum Vergleich, ELSTER rechnet den Wert selbst</div>
            </div>
            <div class="zahl text-2xl font-bold">{{ betrag(bericht.zahllast) }} €</div>
          </div>
        </StepPanel>

        <!-- 5. Protokoll -->
        <StepPanel :value="5">
          <h3 class="mb-2 font-semibold">Übertragungsprotokoll ablegen</h3>
          <p class="mb-4 text-muted-color">
            Das PDF, das ELSTER nach dem Absenden anbietet (später auch unter Postfach), hier hochladen. Es landet in
            Paperless und gehört damit in die Unterlagen für die Steuerberatung; die Karte „Nächste Abgabe“ springt auf den
            nächsten Monat.
          </p>
          <Button
            label="Protokoll nach Paperless"
            icon="pi pi-cloud-upload"
            class="mb-4"
            :loading="beschaeftigt.protokoll"
            :disabled="!status.paperless"
            v-tooltip.top="status.paperless ? '' : 'Paperless ist nicht eingerichtet'"
            @click="protokollWahl?.click()"
          />
          <input ref="protokollWahl" type="file" accept=".pdf,application/pdf" hidden @change="ablegen($event, 'protokoll')" />
          <p v-if="stand && stand.protokoll !== null" class="status text-sm">
            <i :class="stand.protokoll ? 'pi pi-check-circle text-green-500' : 'pi pi-circle text-muted-color'" class="mr-2" />
            ELSTER-Protokoll für {{ monatsname }} {{ stand.protokoll ? "liegt in Paperless" : "noch nicht in Paperless" }}
          </p>
        </StepPanel>

        <!-- 6. Steuerberatung -->
        <StepPanel :value="6">
          <h3 class="mb-2 font-semibold">Unterlagen an die Steuerberatung</h3>
          <ol class="mb-4 list-decimal space-y-1 pl-5">
            <li>ZIP herunterladen (Belege, Kontoauszug, Protokoll, CSV-Dateien und eine Liste, was fehlt).</li>
            <li>
              E-Mail vorbereiten: das Mailprogramm öffnet einen Entwurf mit Empfänger, Betreff und Text. Die ZIP aus dem
              Download-Ordner anhängen und selbst absenden; die App verschickt nichts.
            </li>
          </ol>
          <Message v-if="stand && stand.protokoll === false" severity="warn" size="small" class="mb-3">
            Das ELSTER-Protokoll liegt noch nicht in Paperless und fehlt dann in der ZIP.
          </Message>
          <div class="mb-4 flex flex-wrap gap-2">
            <a :href="`/api/export/${zeitraum}/unterlagen.zip`">
              <Button label="ZIP herunterladen" icon="pi pi-download" severity="secondary" />
            </a>
            <a v-if="stand?.mail.an" :href="mailLink" @click="mailVorbereitet = true">
              <Button label="E-Mail vorbereiten" icon="pi pi-envelope" />
            </a>
            <Button v-else label="E-Mail vorbereiten" icon="pi pi-envelope" disabled
                    v-tooltip.top="'Erst unten die Empfängeradresse eintragen'" />
            <Button :label="vorlageOffen ? 'Vorlage schließen' : 'Vorlage bearbeiten'" icon="pi pi-pencil" severity="secondary"
                    text @click="vorlageOffen = !vorlageOffen" />
          </div>
          <div v-if="stand && !vorlageOffen" class="rounded-lg bg-surface-100 p-3 text-sm dark:bg-surface-800">
            <div><span class="text-muted-color">An:</span> {{ stand.mail.an || "–" }}</div>
            <div><span class="text-muted-color">Betreff:</span> {{ ersetzen(stand.mail.betreff) }}</div>
            <div class="mt-2 whitespace-pre-line">{{ ersetzen(stand.mail.text) }}</div>
          </div>
          <form v-if="vorlageOffen" class="flex max-w-2xl flex-col gap-3" @submit.prevent="vorlageSpeichern">
            <label class="flex flex-col gap-1 text-sm">
              An
              <InputText v-model="vorlage.an" type="email" placeholder="name@example.de" fluid />
            </label>
            <label class="flex flex-col gap-1 text-sm">
              Betreff
              <InputText v-model="vorlage.betreff" fluid />
            </label>
            <label class="flex flex-col gap-1 text-sm">
              Text
              <Textarea v-model="vorlage.text" rows="6" auto-resize fluid />
            </label>
            <p class="text-xs text-muted-color">{monat} und {jahr} werden durch den Monat ersetzt, z. B. „{{ monatsname }}“ und „{{ jahr }}“.</p>
            <div>
              <Button type="submit" label="Vorlage speichern" icon="pi pi-save" :loading="beschaeftigt.mail" :disabled="!vorlageGeaendert" />
            </div>
          </form>
        </StepPanel>

        <!-- 7. Stundenzettel -->
        <StepPanel :value="7">
          <h3 class="mb-2 font-semibold">Stundenzettel für den Minijob</h3>
          <p class="mb-4 text-muted-color">
            Trägt die Monatsstunden auf die Arbeitstage von {{ monatsname }} ein (wie im Reiter Stundenzettel). Für Urlaub
            oder andere Tage dort anpassen.
          </p>
          <p v-if="stand" class="status mb-4 text-sm">
            <i :class="Number(stand.stundenzettel) ? 'pi pi-check-circle text-green-500' : 'pi pi-circle text-muted-color'" class="mr-2" />
            {{ Number(stand.stundenzettel) ? `${stunden(stand.stundenzettel)} Stunden im ${monatsname} eingetragen` : `Für ${monatsname} noch nichts eingetragen` }}
          </p>
          <div class="flex flex-wrap gap-2">
            <Button v-if="stand && !Number(stand.stundenzettel)" label="Vorschlag eintragen" icon="pi pi-check"
                    :loading="beschaeftigt.stunden" @click="stundenEintragen" />
            <a :href="`/api/stundenzettel/${jahr}/excel`">
              <Button label="Excel herunterladen" icon="pi pi-file-excel" severity="secondary" />
            </a>
            <Button label="Im Reiter Stundenzettel anpassen" icon="pi pi-arrow-right" icon-pos="right" severity="secondary" text
                    @click="emit('oeffnen', 'stundenzettel', zeitraum)" />
          </div>
        </StepPanel>
      </StepPanels>
    </Stepper>

    <div class="mt-2 flex justify-between gap-2 border-t border-surface-200 pt-4 dark:border-surface-700">
      <Button label="Zurück" icon="pi pi-arrow-left" severity="secondary" :disabled="schritt <= 1" @click="schritt--" />
      <Button v-if="schritt < SCHRITTE.length" label="Weiter" icon="pi pi-arrow-right" icon-pos="right" @click="schritt++" />
      <Button v-else label="Abschluss beenden" icon="pi pi-flag" @click="beenden" />
    </div>
  </KarteBox>
</template>

<style scoped>
/* Sieben Schritte passen auf schmalen Bildschirmen nicht nebeneinander: dort nur den aktuellen benennen */
.monatsabschluss :deep(.p-steplist) {
  overflow-x: auto;
}
@media (max-width: 640px) {
  .monatsabschluss :deep(.p-step:not(.p-step-active) .p-step-title) {
    display: none;
  }
}
.status > li,
p.status {
  display: flex;
  align-items: baseline;
}
</style>
