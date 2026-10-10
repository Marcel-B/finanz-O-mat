<script setup lang="ts">
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Message from "primevue/message";
import Select from "primevue/select";
import { useToast } from "primevue/usetoast";
import { computed, ref, watch } from "vue";

import { api, type Jahresuebersicht, type Status } from "@/api";
import { betrag } from "@/format";

import KarteBox from "./KarteBox.vue";
import MonatsChart from "./MonatsChart.vue";

const props = defineProps<{ status: Status }>();
const toast = useToast();

const aktuellesJahr = Number(props.status.frist.zeitraum.slice(0, 4));
const jahr = ref(aktuellesJahr);
const jahre = Array.from({ length: 5 }, (_, i) => aktuellesJahr - i);
const daten = ref<Jahresuebersicht | null>(null);

watch(jahr, async (j) => {
  try {
    daten.value = await api.uebersicht(j);
  } catch (e) {
    toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
  }
}, { immediate: true });

const kacheln = computed(() => {
  const s = daten.value?.summe;
  if (!s) return [];
  return [
    { titel: "Einnahmen netto", wert: s.einnahmen, icon: "pi pi-arrow-down-left" },
    { titel: "Ausgaben netto", wert: s.ausgaben, icon: "pi pi-arrow-up-right" },
    { titel: "Ergebnis", wert: s.ergebnis, icon: "pi pi-wallet" },
    { titel: "USt-Zahllast", wert: s.zahllast, icon: "pi pi-building-columns" },
  ];
});
const mitBelegen = computed(() => daten.value?.monate.filter((m) => m.belege > 0) ?? []);
</script>

<template>
  <div class="mb-4 flex flex-wrap items-end gap-2">
    <label class="flex flex-col gap-1 text-sm text-muted-color">
      Jahr
      <Select v-model="jahr" :options="jahre" class="w-32" />
    </label>
    <span class="flex-1" />
    <a :href="`/api/uebersicht/${jahr}/csv`">
      <Button label="CSV Übersicht" icon="pi pi-file-export" severity="secondary" text />
    </a>
  </div>

  <Message severity="secondary" class="mb-4">
    Grober Überblick für dich, keine Gewinnermittlung fürs Finanzamt. Beträge netto ohne Umsatzsteuer, zugeordnet wie
    in der Voranmeldung ({{ daten?.versteuerung === "ist" ? "Ist-Versteuerung: Einnahmen nach Zahlungseingang" : "nach Rechnungsdatum" }}).
  </Message>
  <Message v-if="daten?.unvollstaendig" severity="warn" class="mb-4">
    {{ daten.unvollstaendig }} Beleg(e) fehlen in der Übersicht, weil ein Umrechnungskurs fehlt oder der Steuerfall
    nicht unterstützt wird.
  </Message>

  <template v-if="daten">
    <div class="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div
        v-for="k in kacheln"
        :key="k.titel"
        class="rounded-xl border border-surface-200 bg-surface-0 p-4 dark:border-surface-700 dark:bg-surface-900"
      >
        <div class="flex items-center gap-2 text-sm text-muted-color"><i :class="k.icon" />{{ k.titel }} {{ jahr }}</div>
        <div class="zahl mt-1 text-2xl font-semibold">{{ betrag(k.wert) }} €</div>
      </div>
    </div>

    <KarteBox titel="Einnahmen und Ausgaben je Monat">
      <MonatsChart :monate="daten.monate" />
    </KarteBox>

    <div class="grid gap-4 lg:grid-cols-3">
      <KarteBox class="lg:col-span-2" titel="Monate">
        <DataTable :value="mitBelegen" size="small" data-key="zeitraum">
          <template #empty><span class="text-muted-color">Noch keine Belege in diesem Jahr.</span></template>
          <Column field="monat" header="Monat" />
          <Column header="Einnahmen" class="text-right">
            <template #body="{ data }"><span class="zahl">{{ betrag(data.einnahmen) }} €</span></template>
          </Column>
          <Column header="Ausgaben" class="text-right">
            <template #body="{ data }"><span class="zahl">{{ betrag(data.ausgaben) }} €</span></template>
          </Column>
          <Column header="Ergebnis" class="text-right">
            <template #body="{ data }"><span class="zahl font-semibold">{{ betrag(data.ergebnis) }} €</span></template>
          </Column>
          <Column header="USt-Zahllast" class="text-right">
            <template #body="{ data }"><span class="zahl">{{ betrag(data.zahllast) }} €</span></template>
          </Column>
          <Column field="belege" header="Belege" class="text-right" />
        </DataTable>
      </KarteBox>

      <div>
        <KarteBox titel="Größte Kunden">
          <p v-if="!daten.kunden.length" class="text-sm text-muted-color">Keine Einnahmen.</p>
          <div v-for="k in daten.kunden" :key="k.name" class="flex justify-between gap-2 border-b border-surface-100 py-1 text-sm dark:border-surface-800">
            <span class="truncate">{{ k.name }}</span><span class="zahl">{{ betrag(k.betrag) }} €</span>
          </div>
        </KarteBox>
        <KarteBox titel="Größte Ausgaben">
          <p v-if="!daten.lieferanten.length" class="text-sm text-muted-color">Keine Ausgaben.</p>
          <div v-for="k in daten.lieferanten" :key="k.name" class="flex justify-between gap-2 border-b border-surface-100 py-1 text-sm dark:border-surface-800">
            <span class="truncate">{{ k.name }}</span><span class="zahl">{{ betrag(k.betrag) }} €</span>
          </div>
        </KarteBox>
      </div>
    </div>
  </template>
</template>
