<script setup lang="ts">
import Button from "primevue/button";
import DatePicker from "primevue/datepicker";
import Dialog from "primevue/dialog";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Select from "primevue/select";
import { useToast } from "primevue/usetoast";
import { computed, reactive, ref, watch } from "vue";

import { api, type StundenzettelJahr, type StundenzettelMonat, type StundenzettelVorschlag } from "@/api";
import { alsIso, ausIso, datum } from "@/format";

import KarteBox from "./KarteBox.vue";

// Feste Stunden je Monat an einem festen Wochentag (Standard: 20 h, mittwochs). Die App schlägt die Tage
// vor, vor dem Eintragen lassen sie sich anpassen, z. B. in der Urlaubszeit.
const toast = useToast();

const WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
const MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
  "November", "Dezember"];
const wochentag = (d: Date) => WOCHENTAGE[(d.getDay() + 6) % 7];
const stunden = (wert: string | number) =>
  Number(wert).toLocaleString("de-DE", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

const heute = new Date();
const jahr = ref(heute.getFullYear());
const jahre = Array.from({ length: 4 }, (_, i) => heute.getFullYear() - i);
const daten = ref<StundenzettelJahr | null>(null);
const zeitraum = ref<string | null>(null);
const zeilen = ref<{ datum: Date | null; stunden: number | null }[]>([]);
const feiertage = ref<StundenzettelVorschlag["feiertage"]>([]);
const geaendert = ref(false);
const speichert = ref(false);
const laedt = ref(false);

const soll = computed(() => Number(daten.value?.einstellungen.stunden ?? 20));
const monat = computed(() => daten.value?.monate.find((m) => m.zeitraum === zeitraum.value) ?? null);
const eingetragen = computed(() => !!monat.value?.tage.length);
const summe = computed(() => zeilen.value.reduce((s, z) => s + (z.stunden ?? 0), 0));
const monatsname = computed(() => {
  if (!zeitraum.value) return "";
  const [j, m] = zeitraum.value.split("-").map(Number);
  return `${MONATE[m - 1]} ${j}`;
});
const grenzen = computed(() => {
  const [j, m] = (zeitraum.value ?? "2000-01").split("-").map(Number);
  return { von: new Date(j, m - 1, 1), bis: new Date(j, m, 0) };
});
const doppelt = computed(() => {
  const tage = zeilen.value.map((z) => alsIso(z.datum)).filter(Boolean);
  return new Set(tage).size !== tage.length;
});
const gueltig = computed(() => zeilen.value.every((z) => z.datum && z.stunden && z.stunden > 0) && !doppelt.value);

function fehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

function uebernehmen(tage: { datum: string; stunden: string }[]) {
  zeilen.value = tage.map((t) => ({ datum: ausIso(t.datum), stunden: Number(t.stunden) }));
  geaendert.value = false;
}

async function vorschlagLaden() {
  if (!zeitraum.value) return;
  try {
    const v = await api.stundenzettelVorschlag(zeitraum.value);
    feiertage.value = v.feiertage;
    uebernehmen(v.tage);
    geaendert.value = eingetragen.value;
  } catch (e) {
    fehler(e);
  }
}

// Eingetragene Monate zeigen, was drinsteht; offene Monate starten mit dem Vorschlag
async function monatZeigen() {
  feiertage.value = [];
  if (monat.value?.tage.length) uebernehmen(monat.value.tage);
  else await vorschlagLaden();
}

async function laden() {
  laedt.value = true;
  try {
    daten.value = await api.stundenzettel(jahr.value);
    const monate = daten.value.monate;
    // Standard: der vergangene Monat, in älteren Jahren der Dezember
    const ziel = monate.find((m) => m.zeitraum === daten.value!.vergangen) ?? monate.at(-1);
    zeitraum.value = ziel?.zeitraum ?? null;
    await monatZeigen();
  } catch (e) {
    fehler(e);
  } finally {
    laedt.value = false;
  }
}

watch(jahr, laden, { immediate: true });

function waehlen(m: StundenzettelMonat) {
  if (m.zeitraum === zeitraum.value) return;
  zeitraum.value = m.zeitraum;
  monatZeigen();
}

// Die Monatsstunden in halben Stunden auf die gewählten Tage verteilen (wie der Vorschlag)
function verteilen() {
  const tage = zeilen.value.filter((z) => z.datum).sort((a, b) => a.datum!.getTime() - b.datum!.getTime());
  if (!tage.length) return;
  const einheiten = Math.round(soll.value * 2);
  const jeTag = Math.floor(einheiten / tage.length);
  const rest = einheiten % tage.length;
  zeilen.value = tage.map((z, i) => ({ datum: z.datum, stunden: (jeTag + (i < rest ? 1 : 0)) / 2 }));
  geaendert.value = true;
}

// Nächster freier Arbeitstag nach dem letzten Tag, sonst der nächste freie Tag im Monat
function tagHinzufuegen() {
  const belegt = new Set(zeilen.value.map((z) => alsIso(z.datum)));
  const ziel = daten.value?.einstellungen.wochentag ?? 2;
  const { von, bis } = grenzen.value;
  const letzter = zeilen.value.reduce<Date | null>((l, z) => (z.datum && (!l || z.datum > l) ? z.datum : l), null);
  const kandidaten: Date[] = [];
  for (let d = new Date(von); d <= bis; d.setDate(d.getDate() + 1)) {
    if (!belegt.has(alsIso(d))) kandidaten.push(new Date(d));
  }
  const nach = kandidaten.filter((d) => !letzter || d > letzter);
  const tag = nach.find((d) => (d.getDay() + 6) % 7 === ziel) ?? nach[0] ?? kandidaten[0] ?? null;
  zeilen.value.push({ datum: tag, stunden: null });
  geaendert.value = true;
}

function entfernen(i: number) {
  zeilen.value.splice(i, 1);
  geaendert.value = true;
}

async function eintragen() {
  if (!zeitraum.value || !daten.value) return;
  speichert.value = true;
  try {
    const tage = zeilen.value.map((z) => ({ datum: alsIso(z.datum)!, stunden: String(z.stunden) }));
    daten.value.monate = await api.stundenzettelEintragen(zeitraum.value, tage);
    uebernehmen(monat.value?.tage ?? []);
    toast.add({ severity: "success", summary: `${monatsname.value} eingetragen`, life: 3000 });
  } catch (e) {
    fehler(e);
  } finally {
    speichert.value = false;
  }
}

// Einstellungen und Übernahme der bisherigen Datei
const einstellungenOffen = ref(false);
const form = reactive({ name: "", stunden: 20 as number | null, wochentag: 2 });
const speichertEinstellungen = ref(false);
const liestEin = ref(false);
const datei = ref<HTMLInputElement | null>(null);
const wochentagOptionen = WOCHENTAGE.map((label, value) => ({ label, value }));

function einstellungenOeffnen() {
  const e = daten.value?.einstellungen;
  form.name = e?.name ?? "";
  form.stunden = Number(e?.stunden ?? 20);
  form.wochentag = e?.wochentag ?? 2;
  einstellungenOffen.value = true;
}

async function einstellungenSpeichern() {
  speichertEinstellungen.value = true;
  try {
    const e = await api.stundenzettelEinstellungenSpeichern({
      name: form.name,
      stunden: String(form.stunden ?? 20),
      wochentag: form.wochentag,
    });
    if (daten.value) daten.value.einstellungen = e;
    einstellungenOffen.value = false;
    if (!eingetragen.value) await vorschlagLaden();
  } catch (e) {
    fehler(e);
  } finally {
    speichertEinstellungen.value = false;
  }
}

async function einlesen(event: Event) {
  const eingabe = event.target as HTMLInputElement;
  const f = eingabe.files?.[0];
  eingabe.value = "";
  if (!f) return;
  liestEin.value = true;
  try {
    const e = await api.stundenzettelImport(jahr.value, f);
    const teile = [
      `${e.tage} Tage aus ${e.monate.length} Monat(en)`,
      e.uebersprungen.length ? `übersprungen, weil schon erfasst: ${e.uebersprungen.join(", ")}` : "",
      e.andere_jahre.length ? `nicht übernommen, Datum aus anderem Jahr: ${e.andere_jahre.join(", ")}` : "",
    ];
    toast.add({ severity: "success", summary: "Excel-Datei übernommen", detail: teile.filter(Boolean).join(" · "), life: 12000 });
    einstellungenOffen.value = false;
    await laden();
  } catch (err) {
    fehler(err);
  } finally {
    liestEin.value = false;
  }
}
</script>

<template>
  <div class="mb-4 flex flex-wrap items-end gap-2">
    <label class="flex flex-col gap-1 text-sm text-muted-color">
      Jahr
      <Select v-model="jahr" :options="jahre" class="w-32" />
    </label>
    <span class="flex-1" />
    <Button label="Einstellungen" icon="pi pi-sliders-h" severity="secondary" text @click="einstellungenOeffnen" />
    <a :href="`/api/stundenzettel/${jahr}/excel`">
      <Button label="Excel erstellen" icon="pi pi-file-excel" :disabled="!daten?.monate.length" />
    </a>
  </div>

  <Message v-if="daten && !daten.einstellungen.name" severity="warn" class="mb-4">
    <div class="flex flex-wrap items-center gap-2">
      <span class="flex-1">
        Noch kein Name für den Kopf hinterlegt. Trag ihn in den Einstellungen ein oder übernimm dort die bisherige
        Excel-Datei.
      </span>
      <Button label="Einstellungen öffnen" size="small" severity="warn" @click="einstellungenOeffnen" />
    </div>
  </Message>

  <div v-if="daten" class="grid gap-4 lg:grid-cols-[1fr_18rem]">
    <KarteBox
      v-if="zeitraum"
      :titel="monatsname"
      :untertitel="eingetragen ? (geaendert ? 'eingetragen, geändert' : 'eingetragen') : 'Vorschlag, noch nicht eingetragen'"
    >
      <template #aktionen>
        <Button
          v-tooltip.top="'Tage neu vorschlagen'"
          label="Vorschlag"
          icon="pi pi-refresh"
          severity="secondary"
          text
          size="small"
          @click="vorschlagLaden"
        />
      </template>

      <Message v-if="feiertage.length" severity="secondary" class="mb-3">
        Ohne Feiertag:
        {{ feiertage.map((f) => `${datum(f.datum).slice(0, 6)} ${f.name}`).join(", ") }}
      </Message>

      <div class="flex flex-col gap-2">
        <div
          v-for="(z, i) in zeilen"
          :key="i"
          class="grid grid-cols-[7rem_1fr_8rem_auto] items-center gap-2 sm:grid-cols-[7rem_12rem_9rem_auto]"
        >
          <span class="text-sm" :class="z.datum ? '' : 'text-muted-color'">{{ z.datum ? wochentag(z.datum) : "–" }}</span>
          <DatePicker
            v-model="z.datum"
            date-format="dd.mm.yy"
            :min-date="grenzen.von"
            :max-date="grenzen.bis"
            show-icon
            fluid
            @update:model-value="geaendert = true"
          />
          <InputNumber
            v-model="z.stunden"
            :min="0.5"
            :max="24"
            :step="0.5"
            :max-fraction-digits="2"
            suffix=" h"
            locale="de-DE"
            show-buttons
            fluid
            @update:model-value="geaendert = true"
          />
          <Button
            v-tooltip.top="'Tag entfernen'"
            icon="pi pi-times"
            severity="secondary"
            text
            size="small"
            aria-label="Tag entfernen"
            @click="entfernen(i)"
          />
        </div>
        <p v-if="!zeilen.length" class="text-sm text-muted-color">Keine Tage in diesem Monat.</p>
      </div>

      <div class="mt-3 flex flex-wrap items-center gap-2">
        <Button label="Tag hinzufügen" icon="pi pi-plus" severity="secondary" outlined size="small" @click="tagHinzufuegen" />
        <Button
          v-tooltip.top="`${stunden(soll)} h gleichmäßig auf die Tage verteilen`"
          label="Gleichmäßig verteilen"
          icon="pi pi-equals"
          severity="secondary"
          outlined
          size="small"
          :disabled="!zeilen.length"
          @click="verteilen"
        />
        <span class="flex-1" />
        <span class="zahl text-sm" :class="summe === soll ? 'text-muted-color' : 'font-semibold text-orange-600 dark:text-orange-400'">
          {{ stunden(summe) }} von {{ stunden(soll) }} h
        </span>
      </div>
      <p v-if="doppelt" class="mt-2 text-sm text-red-600 dark:text-red-400">Ein Tag steht doppelt drin.</p>

      <div class="mt-4 flex flex-wrap items-center gap-2">
        <Button
          :label="eingetragen ? 'Änderungen speichern' : `${monatsname} eintragen`"
          :icon="eingetragen ? 'pi pi-save' : 'pi pi-check'"
          :loading="speichert"
          :disabled="!gueltig || (eingetragen && !geaendert)"
          @click="eintragen"
        />
        <span class="text-xs text-muted-color">Steht danach im Excel-Blatt für {{ monatsname.split(" ")[0] }}.</span>
      </div>
    </KarteBox>

    <KarteBox :titel="`Jahr ${jahr}`" untertitel="bis heute">
      <ul class="flex flex-col">
        <li v-for="m in daten.monate" :key="m.zeitraum">
          <button
            type="button"
            class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-surface-100 dark:hover:bg-surface-800"
            :class="m.zeitraum === zeitraum ? 'bg-primary-50 font-semibold dark:bg-primary-950' : ''"
            @click="waehlen(m)"
          >
            <i
              class="pi text-xs"
              :class="!m.tage.length ? 'pi-circle text-muted-color'
                : Number(m.stunden) === soll ? 'pi-check-circle text-green-500' : 'pi-exclamation-circle text-orange-500'"
            />
            <span class="flex-1">{{ m.name }}</span>
            <span class="zahl" :class="m.tage.length ? '' : 'text-muted-color'">
              {{ m.tage.length ? `${stunden(m.stunden)} h` : "offen" }}
            </span>
          </button>
        </li>
      </ul>
    </KarteBox>
  </div>
  <p v-else-if="laedt" class="text-muted-color">Lädt …</p>

  <Dialog v-model:visible="einstellungenOffen" modal header="Stundenzettel" class="w-[min(640px,calc(100vw-2rem))]">
    <form class="flex flex-col gap-3" @submit.prevent="einstellungenSpeichern">
      <div class="flex flex-wrap items-center gap-2 rounded-lg bg-surface-100 p-3 text-sm dark:bg-surface-800">
        <span class="flex-1">
          Bisherige Excel-Datei übernehmen: liest Name und alle Tage aus {{ jahr }} ein. Zeilen mit einem Datum aus einem
          anderen Jahr und Monate, die hier schon eingetragen sind, bleiben außen vor.
        </span>
        <Button
          label="Excel-Datei übernehmen"
          icon="pi pi-file-import"
          severity="secondary"
          size="small"
          :loading="liestEin"
          @click="datei?.click()"
        />
        <input
          ref="datei"
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          hidden
          @change="einlesen"
        />
      </div>
      <label class="flex flex-col gap-1 text-sm">
        Name (oben links im Blatt)
        <InputText v-model="form.name" placeholder="Nachname Vorname" fluid />
      </label>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1 text-sm">
          Stunden je Monat
          <InputNumber v-model="form.stunden" :min="0.5" :max="300" :step="0.5" :max-fraction-digits="2" locale="de-DE" suffix=" h" fluid />
        </label>
        <label class="flex flex-col gap-1 text-sm">
          Arbeitstag
          <Select v-model="form.wochentag" :options="wochentagOptionen" option-label="label" option-value="value" fluid />
        </label>
      </div>
      <div class="flex justify-end gap-2">
        <Button label="Abbrechen" severity="secondary" text @click="einstellungenOffen = false" />
        <Button type="submit" label="Speichern" icon="pi pi-save" :loading="speichertEinstellungen" :disabled="!form.stunden" />
      </div>
    </form>
  </Dialog>
</template>
