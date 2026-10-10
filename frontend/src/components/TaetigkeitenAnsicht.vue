<script setup lang="ts">
import AutoComplete from "primevue/autocomplete";
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import DatePicker from "primevue/datepicker";
import Dialog from "primevue/dialog";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Select from "primevue/select";
import { useConfirm } from "primevue/useconfirm";
import { useToast } from "primevue/usetoast";
import { computed, onMounted, reactive, ref, watch } from "vue";

import { api, type Taetigkeit, type TaetigkeitEingabe, type TaetigkeitenMonat, type TaetigkeitVorlage } from "@/api";
import { alsIso, ausIso, betrag, datum, zeitraumVon } from "@/format";

import KarteBox from "./KarteBox.vue";
import TaetigkeitVorlageForm from "./TaetigkeitVorlage.vue";

const toast = useToast();
const confirm = useConfirm();

const heute = new Date();
const monat = ref<Date>(new Date(heute.getFullYear(), heute.getMonth(), 1));
const zeitraum = computed(() => zeitraumVon(monat.value));
const daten = ref<TaetigkeitenMonat | null>(null);
const vorlage = ref<TaetigkeitVorlage | null>(null);
const vorlageOffen = ref(false);
const speichert = ref(false);
const bearbeitet = ref<number | null>(null);
const vorschlaege = ref<string[]>([]);
const formular = ref<HTMLElement | null>(null);

const form = reactive({
  datum: new Date() as Date | null,
  stunden: 8 as number | null,
  auftrag: "",
  referenz: "",
  taetigkeit: "",
  rolle: "",
  ort: "",
  leistungsart: "",
});

const WOCHENTAGE = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const wochentag = (iso: string) => WOCHENTAGE[ausIso(iso)?.getDay() ?? 0];
const tagKurz = (iso: string) => `${wochentag(iso)} ${datum(iso).slice(0, 6)}`;
const stunden = (wert: string | number) =>
  Number(wert).toLocaleString("de-DE", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

// Auswahllisten aus der Vorlage, ergänzt um Werte, die in alten Einträgen vorkommen
function liste(ausVorlage: string[] | undefined, feld: "auftrag" | "rolle" | "ort" | "leistungsart") {
  const werte = new Set(ausVorlage ?? []);
  for (const e of daten.value?.eintraege ?? []) if (e[feld]) werte.add(e[feld]);
  if (form[feld]) werte.add(form[feld]);
  return [...werte];
}
const auftraege = computed(() => liste(vorlage.value?.auftraege.map((a) => a.name), "auftrag"));
const rollen = computed(() => liste(vorlage.value?.rollen, "rolle"));
const orte = computed(() => liste(vorlage.value?.orte, "ort"));
const leistungsarten = computed(() => liste(vorlage.value?.leistungsarten, "leistungsart"));
const tage = computed(() => new Set(daten.value?.eintraege.map((e) => e.datum)).size);
const ohneSatz = computed(() => vorlage.value && vorlage.value.stundensatz == null);

function fehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

function fuellen(t: TaetigkeitEingabe) {
  form.datum = ausIso(t.datum);
  form.stunden = Number(t.stunden);
  form.auftrag = t.auftrag;
  form.referenz = t.referenz;
  form.taetigkeit = t.taetigkeit;
  form.rolle = t.rolle;
  form.ort = t.ort;
  form.leistungsart = t.leistungsart;
}

async function laden(vorbelegen = false) {
  try {
    daten.value = await api.taetigkeiten(zeitraum.value);
    if (vorbelegen && bearbeitet.value === null) fuellen(daten.value.vorschlag);
  } catch (e) {
    fehler(e);
  }
}

async function vorlageLaden() {
  try {
    vorlage.value = await api.taetigkeitVorlage();
  } catch (e) {
    fehler(e);
  }
}

onMounted(vorlageLaden);
watch(zeitraum, () => {
  bearbeitet.value = null;
  laden(true);
}, { immediate: true });

function suchen(event: { query: string }) {
  const q = event.query.trim().toLowerCase();
  const texte = daten.value?.texte.map((t) => t.taetigkeit) ?? [];
  vorschlaege.value = (q ? texte.filter((t) => t.toLowerCase().includes(q)) : texte).slice(0, 30);
}

// Eine frühere Tätigkeit gehört meist zum selben Auftrag und Ticket
function textGewaehlt(event: { value: string }) {
  const t = daten.value?.texte.find((x) => x.taetigkeit === event.value);
  if (!t) return;
  if (t.auftrag) form.auftrag = t.auftrag;
  form.referenz = t.referenz;
}

async function speichern() {
  if (!form.datum || !form.stunden) return;
  const eingabe: TaetigkeitEingabe = {
    datum: alsIso(form.datum)!,
    stunden: String(form.stunden),
    auftrag: form.auftrag ?? "",
    referenz: form.referenz ?? "",
    taetigkeit: form.taetigkeit ?? "",
    rolle: form.rolle ?? "",
    ort: form.ort ?? "",
    leistungsart: form.leistungsart ?? "",
  };
  speichert.value = true;
  try {
    if (bearbeitet.value === null) await api.taetigkeitAnlegen(eingabe);
    else await api.taetigkeitAendern(bearbeitet.value, eingabe);
    bearbeitet.value = null;
    const ziel = zeitraumVon(form.datum);
    if (ziel !== zeitraum.value) {
      toast.add({ severity: "info", summary: `Eingetragen für ${datum(eingabe.datum)}`, life: 3000 });
      monat.value = ausIso(`${ziel}-01`)!;
    } else {
      await laden(true);
    }
  } catch (e) {
    fehler(e);
  } finally {
    speichert.value = false;
  }
}

function zumFormular() {
  formular.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function bearbeiten(t: Taetigkeit) {
  fuellen(t);
  bearbeitet.value = t.id;
  zumFormular();
}

// Eintrag als Vorlage für den nächsten freien Tag
function uebernehmen(t: Taetigkeit) {
  bearbeitet.value = null;
  fuellen({ ...t, datum: daten.value?.vorschlag.datum ?? t.datum, stunden: daten.value?.vorschlag.stunden ?? t.stunden });
  zumFormular();
}

function abbrechen() {
  bearbeitet.value = null;
  if (daten.value) fuellen(daten.value.vorschlag);
}

function loeschen(t: Taetigkeit) {
  confirm.require({
    header: "Eintrag löschen",
    message: `Eintrag vom ${datum(t.datum)} wirklich löschen?`,
    icon: "pi pi-trash",
    acceptProps: { label: "Löschen", severity: "danger" },
    rejectProps: { label: "Abbrechen", severity: "secondary", outlined: true },
    accept: async () => {
      try {
        await api.taetigkeitLoeschen(t.id);
        if (bearbeitet.value === t.id) bearbeitet.value = null;
        await laden(bearbeitet.value === null);
      } catch (e) {
        fehler(e);
      }
    },
  });
}

function vorlageGespeichert(v: TaetigkeitVorlage) {
  vorlage.value = v;
  vorlageOffen.value = false;
  laden(bearbeitet.value === null);
}

async function importiert() {
  await vorlageLaden();
  await laden(bearbeitet.value === null);
}
</script>

<template>
  <div class="mb-4 flex flex-wrap items-end gap-2">
    <label class="flex flex-col gap-1 text-sm text-muted-color">
      Monat
      <DatePicker v-model="monat" view="month" date-format="MM yy" show-icon :manual-input="false" class="w-52" />
    </label>
    <span class="flex-1" />
    <Button label="Vorlage" icon="pi pi-sliders-h" severity="secondary" text @click="vorlageOffen = true" />
    <a :href="`/api/taetigkeiten/nachweis/${zeitraum}`">
      <Button label="Excel erstellen" icon="pi pi-file-excel" :disabled="!daten?.eintraege.length" />
    </a>
  </div>

  <Message v-if="ohneSatz" severity="warn" class="mb-4">
    <div class="flex flex-wrap items-center gap-2">
      <span class="flex-1">
        Noch kein Stundensatz hinterlegt. Trag ihn in der Vorlage ein oder übernimm dort deine bisherige Excel-Datei.
      </span>
      <Button label="Vorlage öffnen" size="small" severity="warn" @click="vorlageOffen = true" />
    </div>
  </Message>

  <div v-if="daten" class="mb-4 grid gap-4 sm:grid-cols-3">
    <div class="rounded-xl border border-surface-200 bg-surface-0 p-4 dark:border-surface-700 dark:bg-surface-900">
      <div class="flex items-center gap-2 text-sm text-muted-color"><i class="pi pi-calendar" />Geleistete Stunden</div>
      <div class="zahl mt-1 text-2xl font-semibold">{{ stunden(daten.stunden) }} h</div>
    </div>
    <div class="rounded-xl border border-surface-200 bg-surface-0 p-4 dark:border-surface-700 dark:bg-surface-900">
      <div class="flex items-center gap-2 text-sm text-muted-color"><i class="pi pi-wallet" />Betrag netto</div>
      <div class="zahl mt-1 text-2xl font-semibold">{{ betrag(daten.betrag) }} €</div>
    </div>
    <div class="rounded-xl border border-surface-200 bg-surface-0 p-4 dark:border-surface-700 dark:bg-surface-900">
      <div class="flex items-center gap-2 text-sm text-muted-color"><i class="pi pi-briefcase" />Arbeitstage</div>
      <div class="zahl mt-1 text-2xl font-semibold">{{ tage }}</div>
    </div>
  </div>

  <div ref="formular" class="scroll-mt-4">
    <KarteBox :titel="bearbeitet === null ? 'Neuer Eintrag' : 'Eintrag bearbeiten'">
      <form class="flex flex-col gap-3" @submit.prevent="speichern">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-[10rem_9rem_1fr_10rem]">
          <label class="flex flex-col gap-1 text-sm">
            Datum
            <DatePicker v-model="form.datum" date-format="dd.mm.yy" show-icon fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Stunden
            <InputNumber
              v-model="form.stunden"
              :min="0.25"
              :max="24"
              :step="0.25"
              :max-fraction-digits="2"
              locale="de-DE"
              show-buttons
              fluid
            />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Auftrag
            <Select v-model="form.auftrag" :options="auftraege" editable fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Referenz
            <InputText v-model="form.referenz" placeholder="Ticket" fluid />
          </label>
        </div>
        <label class="flex flex-col gap-1 text-sm">
          Tätigkeit
          <AutoComplete
            v-model="form.taetigkeit"
            :suggestions="vorschlaege"
            dropdown
            fluid
            placeholder="Was hast du gemacht? Frühere Tätigkeiten erscheinen beim Tippen."
            @complete="suchen"
            @option-select="textGewaehlt"
          />
        </label>
        <div class="grid gap-3 sm:grid-cols-3">
          <label class="flex flex-col gap-1 text-sm">
            Rolle
            <Select v-model="form.rolle" :options="rollen" editable fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Ort
            <Select v-model="form.ort" :options="orte" editable fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Leistungsart
            <Select v-model="form.leistungsart" :options="leistungsarten" editable fluid />
          </label>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <Button
            type="submit"
            :label="bearbeitet === null ? 'Eintragen' : 'Speichern'"
            :icon="bearbeitet === null ? 'pi pi-plus' : 'pi pi-save'"
            :loading="speichert"
            :disabled="!form.datum || !form.stunden"
          />
          <Button v-if="bearbeitet !== null" label="Abbrechen" severity="secondary" text @click="abbrechen" />
          <span v-else class="text-xs text-muted-color">
            Vorbelegt mit dem letzten Eintrag und dem nächsten freien Werktag.
          </span>
        </div>
      </form>
    </KarteBox>
  </div>

  <Message v-if="daten?.fehlende_tage.length" severity="secondary" class="mb-4">
    <div class="flex flex-wrap items-center gap-1">
      <span class="mr-1">Werktage ohne Eintrag:</span>
      <Button
        v-for="t in daten.fehlende_tage"
        :key="t"
        :label="tagKurz(t)"
        size="small"
        severity="secondary"
        text
        class="!px-2 !py-0.5"
        @click="bearbeitet = null; form.datum = ausIso(t); zumFormular()"
      />
    </div>
  </Message>

  <KarteBox titel="Einträge" :untertitel="daten ? `${daten.eintraege.length} im Monat` : ''">
    <DataTable :value="daten?.eintraege ?? []" size="small" data-key="id">
      <template #empty><span class="text-muted-color">Noch nichts eingetragen.</span></template>
      <Column header="Datum" class="whitespace-nowrap">
        <template #body="{ data }">{{ tagKurz(data.datum) }}</template>
      </Column>
      <Column field="auftrag" header="Auftrag" />
      <Column field="referenz" header="Referenz" />
      <Column header="Tätigkeit">
        <template #body="{ data }">
          <div>{{ data.taetigkeit }}</div>
          <div class="text-xs text-muted-color">{{ [data.rolle, data.ort, data.leistungsart].filter(Boolean).join(" · ") }}</div>
        </template>
      </Column>
      <Column header="Stunden" class="text-right">
        <template #body="{ data }"><span class="zahl">{{ stunden(data.stunden) }}</span></template>
      </Column>
      <Column header="Betrag" class="text-right">
        <template #body="{ data }"><span class="zahl">{{ betrag(data.betrag) }} €</span></template>
      </Column>
      <Column class="w-0 whitespace-nowrap text-right">
        <template #body="{ data }">
          <Button
            v-tooltip.top="'Als Vorlage für den nächsten Tag'"
            icon="pi pi-copy"
            severity="secondary"
            text
            size="small"
            aria-label="Als Vorlage übernehmen"
            @click="uebernehmen(data)"
          />
          <Button
            v-tooltip.top="'Bearbeiten'"
            icon="pi pi-pencil"
            severity="secondary"
            text
            size="small"
            aria-label="Bearbeiten"
            @click="bearbeiten(data)"
          />
          <Button
            v-tooltip.top="'Löschen'"
            icon="pi pi-trash"
            severity="secondary"
            text
            size="small"
            aria-label="Löschen"
            @click="loeschen(data)"
          />
        </template>
      </Column>
    </DataTable>
  </KarteBox>

  <Dialog
    v-model:visible="vorlageOffen"
    modal
    header="Vorlage für den Tätigkeitsnachweis"
    class="w-[min(900px,calc(100vw-2rem))]"
  >
    <TaetigkeitVorlageForm @gespeichert="vorlageGespeichert" @importiert="importiert" />
  </Dialog>
</template>
