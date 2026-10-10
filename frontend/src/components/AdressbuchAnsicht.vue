<script setup lang="ts">
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Dialog from "primevue/dialog";
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Select from "primevue/select";
import SelectButton from "primevue/selectbutton";
import Tag from "primevue/tag";
import Textarea from "primevue/textarea";
import { useConfirm } from "primevue/useconfirm";
import { computed, onMounted, reactive, ref } from "vue";

import { api, type Kontakt, type KontaktEingabe, type Rolle } from "@/api";
import { datum } from "@/format";

const confirm = useConfirm();

const kontakte = ref<Kontakt[]>([]);
const laedt = ref(false);
const fehler = ref("");
const suche = ref("");
const filter = ref<"alle" | "kunde" | "lieferant">("alle");
const filterOptionen = [
  { label: "Alle", value: "alle" },
  { label: "Kunden", value: "kunde" },
  { label: "Lieferanten", value: "lieferant" },
];
const rollen: { label: string; value: Rolle }[] = [
  { label: "Kunde", value: "kunde" },
  { label: "Lieferant", value: "lieferant" },
  { label: "beides", value: "beides" },
];
const ROLLE_TEXT: Record<string, string> = { kunde: "Kunde", lieferant: "Lieferant", beides: "Kunde und Lieferant" };

async function laden() {
  laedt.value = true;
  try {
    kontakte.value = await api.kontakte();
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    laedt.value = false;
  }
}
onMounted(laden);

const gefiltert = computed(() => {
  const s = suche.value.trim().toLowerCase();
  return kontakte.value.filter((k) => {
    if (filter.value !== "alle" && k.rolle !== filter.value && k.rolle !== "beides") return false;
    if (!s) return true;
    return [k.name, k.ort, k.ustid, k.kundennummer, k.email, k.ansprechpartner, ...k.aliase]
      .some((t) => t.toLowerCase().includes(s));
  });
});
const mitAnschrift = computed(() => kontakte.value.filter((k) => k.strasse || k.ort).length);

// Bearbeiten
const sichtbar = ref(false);
const aktuell = ref<Kontakt | null>(null);
const form = reactive<KontaktEingabe>(leer());
const speichert = ref(false);
const dialogFehler = ref("");
const zusammenMit = ref<number | null>(null);

function leer(): KontaktEingabe {
  return {
    name: "", rolle: "", kundennummer: "", ustid: "", strasse: "", plz: "", ort: "", land: "DE", email: "",
    telefon: "", ansprechpartner: "", notiz: "",
  };
}

function bearbeiten(k: Kontakt | null) {
  aktuell.value = k;
  Object.assign(form, leer(), k ? Object.fromEntries(Object.keys(leer()).map((f) => [f, k[f as keyof KontaktEingabe]])) : {});
  dialogFehler.value = "";
  zusammenMit.value = null;
  sichtbar.value = true;
}

const andere = computed(() =>
  kontakte.value
    .filter((k) => k.id !== aktuell.value?.id)
    .map((k) => ({ label: k.ort ? `${k.name} (${k.ort})` : k.name, value: k.id })),
);

async function speichern() {
  speichert.value = true;
  dialogFehler.value = "";
  try {
    if (aktuell.value) await api.kontaktAendern(aktuell.value.id, form);
    else await api.kontaktAnlegen(form);
    sichtbar.value = false;
    await laden();
  } catch (e) {
    dialogFehler.value = (e as Error).message;
  } finally {
    speichert.value = false;
  }
}

function zusammenfuehren() {
  const ziel = aktuell.value;
  const quelle = kontakte.value.find((k) => k.id === zusammenMit.value);
  if (!ziel || !quelle) return;
  confirm.require({
    header: "Zusammenführen",
    message: `„${quelle.name}“ in „${ziel.name}“ aufgehen lassen? Leere Felder werden ergänzt, die Belege beider ` +
      "Schreibweisen zählen künftig hier.",
    acceptLabel: "Zusammenführen",
    rejectLabel: "Abbrechen",
    accept: async () => {
      try {
        await api.kontaktAendern(ziel.id, form);
        await api.kontakteZusammenfuehren(ziel.id, quelle.id);
        sichtbar.value = false;
        await laden();
      } catch (e) {
        dialogFehler.value = (e as Error).message;
      }
    },
  });
}

async function ausblenden() {
  if (!aktuell.value) return;
  await api.kontaktAusblenden(aktuell.value.id);
  sichtbar.value = false;
  await laden();
}
</script>

<template>
  <div class="mb-4 flex flex-wrap items-center gap-2">
    <IconField>
      <InputIcon class="pi pi-search" />
      <InputText v-model="suche" placeholder="Name, Ort, USt-IdNr. …" class="w-72" />
    </IconField>
    <SelectButton v-model="filter" :options="filterOptionen" option-label="label" option-value="value" :allow-empty="false" />
    <Button label="Kontakt hinzufügen" icon="pi pi-plus" severity="secondary" @click="bearbeiten(null)" />
    <span class="flex-1" />
    <a href="/api/kontakte/csv">
      <Button label="CSV" icon="pi pi-download" severity="secondary" text />
    </a>
  </div>
  <Message v-if="fehler" severity="error" class="mb-4">{{ fehler }}</Message>
  <p class="mb-3 text-sm text-muted-color">
    {{ kontakte.length }} Kontakte, {{ mitAnschrift }} mit Anschrift. Neue Partner aus Belegen kommen von selbst dazu;
    Anschriften stammen aus sevDesk und E-Rechnungen oder werden hier ergänzt.
  </p>
  <DataTable
    :value="gefiltert"
    :loading="laedt"
    size="small"
    data-key="id"
    row-hover
    scrollable
    paginator
    :rows="50"
    :row-class="() => 'cursor-pointer'"
    @row-click="bearbeiten($event.data)"
  >
    <template #empty><span class="text-muted-color">Keine Kontakte gefunden.</span></template>
    <Column header="Name" field="name" sortable>
      <template #body="{ data }">
        <span class="font-medium">{{ data.name }}</span>
        <div v-if="data.ansprechpartner" class="text-xs text-muted-color">{{ data.ansprechpartner }}</div>
        <div v-if="data.aliase.length" class="text-xs text-muted-color">auch: {{ data.aliase.join(", ") }}</div>
      </template>
    </Column>
    <Column header="Rolle">
      <template #body="{ data }">
        <Tag
          v-if="data.rolle"
          :value="ROLLE_TEXT[data.rolle]"
          :severity="data.rolle === 'kunde' ? 'info' : data.rolle === 'beides' ? 'warn' : 'secondary'"
        />
      </template>
    </Column>
    <Column header="Anschrift">
      <template #body="{ data }">
        <template v-if="data.strasse || data.ort">
          {{ data.strasse }}<br v-if="data.strasse" />{{ [data.plz, data.ort].filter(Boolean).join(" ") }}
          <span v-if="data.land && data.land !== 'DE'" class="text-muted-color"> · {{ data.land }}</span>
        </template>
        <span v-else class="text-muted-color">–</span>
      </template>
    </Column>
    <Column header="USt-IdNr. / Kd-Nr.">
      <template #body="{ data }">
        {{ data.ustid || "–" }}
        <div v-if="data.kundennummer" class="text-xs text-muted-color">Kd-Nr. {{ data.kundennummer }}</div>
      </template>
    </Column>
    <Column header="Kontakt">
      <template #body="{ data }">
        <a v-if="data.email" :href="`mailto:${data.email}`" class="text-primary" @click.stop>{{ data.email }}</a>
        <div v-if="data.telefon" class="text-xs text-muted-color">{{ data.telefon }}</div>
      </template>
    </Column>
    <Column header="Belege" field="belege" sortable class="text-right">
      <template #body="{ data }">
        {{ data.belege }}
        <div v-if="data.letzter_beleg" class="whitespace-nowrap text-xs text-muted-color">zuletzt {{ datum(data.letzter_beleg) }}</div>
      </template>
    </Column>
  </DataTable>

  <Dialog v-model:visible="sichtbar" modal :header="aktuell ? aktuell.name : 'Neuer Kontakt'" class="w-full max-w-2xl">
    <form class="grid grid-cols-1 gap-3 sm:grid-cols-6" @submit.prevent="speichern">
      <label class="flex flex-col gap-1 text-sm sm:col-span-4">Name<InputText v-model="form.name" required /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-2">
        Rolle
        <Select v-model="form.rolle" :options="rollen" option-label="label" option-value="value" show-clear placeholder="–" />
      </label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-6">Straße<InputText v-model="form.strasse" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-2">PLZ<InputText v-model="form.plz" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-3">Ort<InputText v-model="form.ort" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-1">Land<InputText v-model="form.land" maxlength="40" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-3">USt-IdNr.<InputText v-model="form.ustid" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-3">Kundennummer<InputText v-model="form.kundennummer" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-3">E-Mail<InputText v-model="form.email" type="email" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-3">Telefon<InputText v-model="form.telefon" /></label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-6">
        Ansprechpartner<InputText v-model="form.ansprechpartner" />
      </label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-6">Notiz<Textarea v-model="form.notiz" rows="2" auto-resize /></label>
      <div v-if="aktuell" class="text-xs text-muted-color sm:col-span-6">
        Herkunft: {{ aktuell.quellen.join(", ") || "–" }} · {{ aktuell.belege }} Belege
        <template v-if="aktuell.aliase.length"> · auch geschrieben: {{ aktuell.aliase.join(", ") }}</template>
      </div>
      <div v-if="aktuell" class="flex flex-wrap items-end gap-2 sm:col-span-6">
        <label class="flex flex-1 flex-col gap-1 text-sm">
          Doppelt? Zusammenführen mit
          <Select v-model="zusammenMit" :options="andere" option-label="label" option-value="value" filter show-clear placeholder="Kontakt wählen" />
        </label>
        <Button label="Zusammenführen" icon="pi pi-link" severity="secondary" :disabled="!zusammenMit" @click="zusammenfuehren" />
      </div>
      <Message v-if="dialogFehler" severity="error" class="sm:col-span-6">{{ dialogFehler }}</Message>
      <div class="flex gap-2 sm:col-span-6">
        <Button
          v-if="aktuell"
          label="Ausblenden"
          icon="pi pi-eye-slash"
          severity="danger"
          text
          v-tooltip.top="'Kontakt nicht mehr anzeigen. Er wird auch aus neuen Belegen nicht wieder angelegt.'"
          @click="ausblenden"
        />
        <span class="flex-1" />
        <Button label="Abbrechen" severity="secondary" text @click="sichtbar = false" />
        <Button type="submit" label="Speichern" icon="pi pi-save" :loading="speichert" />
      </div>
    </form>
  </Dialog>
</template>
