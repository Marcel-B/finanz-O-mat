<script setup lang="ts">
// Bestand eines Depots im Reiter „Konten“ (ING-Direkt-Depot über FinTS). Nur Bestand, Umsätze liefert die ING nicht.
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Message from "primevue/message";
import { computed, ref, watch } from "vue";

import { api, type Depot, type DepotPosition, type DepotZugang } from "@/api";
import { betrag, datum, passtZurSuche, suchtext } from "@/format";

import SuchFeld from "./SuchFeld.vue";

const props = defineProps<{
  depot: Depot;
  zugang: DepotZugang;
  ruftAb: boolean;
  abrufzeiten: string;
  zeit: (iso: string | null | undefined) => string;
}>();
const emit = defineEmits<{ abrufen: [] }>();

const positionen = ref<DepotPosition[]>([]);
const laedt = ref(false);
const fehler = ref("");
const suche = ref("");

async function laden() {
  laedt.value = true;
  fehler.value = "";
  try {
    positionen.value = (await api.depotBestand(props.depot.schluessel)).positionen;
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    laedt.value = false;
  }
}
// Neu laden bei anderem Depot und nach jedem Abruf
watch(() => [props.depot.schluessel, props.depot.abgerufen], laden, { immediate: true });

const waehrung = (w: string) => (w === "EUR" ? "€" : w);

function einstandswert(p: DepotPosition): number | null {
  return p.einstand === null ? null : Number(p.stueck) * Number(p.einstand);
}

function ergebnis(wert: string | null, einstand: number | null): { betrag: number; prozent: number } | null {
  if (wert === null || einstand === null || einstand === 0) return null;
  const b = Number(wert) - einstand;
  return { betrag: b, prozent: (b / einstand) * 100 };
}

const gesamt = computed(() =>
  ergebnis(props.depot.wert, props.depot.einstandswert === null ? null : Number(props.depot.einstandswert)),
);

const prozent = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2, signDisplay: "always" });
const stueck = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 6 });
const farbe = (b: number) => (b < 0 ? "text-red-600 dark:text-red-400" : "text-green-700 dark:text-green-400");

const zeilen = computed(() => positionen.value.map((p) => ({ ...p, ergebnis: ergebnis(p.wert, einstandswert(p)) })));
const gefiltert = computed(() =>
  suche.value.trim()
    ? zeilen.value.filter((p) => passtZurSuche(suchtext(p.name, p.isin, p.wert), suche.value.trim()))
    : zeilen.value,
);
</script>

<template>
  <section
    class="min-w-0 rounded-xl border border-surface-200 bg-surface-0 p-4 lg:flex lg:min-h-0 lg:flex-col dark:border-surface-700 dark:bg-surface-900"
  >
    <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <h2 class="text-lg font-semibold">{{ depot.name }}</h2>
        <div class="text-sm text-muted-color">{{ depot.bank }} · {{ depot.positionen }} Positionen</div>
        <div class="text-sm text-muted-color">
          Kurse vom {{ datum(depot.stand) }} · abgerufen {{ zeit(depot.abgerufen) }}
          <template v-if="abrufzeiten"> · automatisch um {{ abrufzeiten }} Uhr</template>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <div class="text-right">
          <div class="text-2xl font-semibold tabular-nums">{{ betrag(depot.wert) }} {{ waehrung(depot.waehrung) }}</div>
          <div v-if="gesamt" class="text-sm tabular-nums" :class="farbe(gesamt.betrag)">
            {{ gesamt.betrag > 0 ? "+" : "" }}{{ betrag(gesamt.betrag) }} € ({{ prozent.format(gesamt.prozent) }} %)
          </div>
        </div>
        <Button
          v-tooltip.bottom="'Fragt die ING nach einer Freigabe, kommt sie in die ING-App'"
          icon="pi pi-refresh"
          label="Depot abrufen"
          size="small"
          severity="secondary"
          :loading="ruftAb"
          :disabled="!zugang.eingerichtet"
          @click="emit('abrufen')"
        />
      </div>
    </div>

    <Message v-if="zugang.freigabe" severity="warn" class="mb-3">
      Die ING wartet auf eine Freigabe in der App. Im Hintergrund fragt der Abruf nicht erneut nach; einmal „Depot
      abrufen“ und in der ING-App bestätigen.
    </Message>
    <Message v-else-if="zugang.fehler" severity="warn" class="mb-3">
      Letzter Abruf ({{ zeit(zugang.versucht) }}) fehlgeschlagen: {{ zugang.fehler }}
    </Message>
    <Message v-if="fehler" severity="error" class="mb-3">{{ fehler }}</Message>

    <div class="mb-2 flex justify-end">
      <SuchFeld v-model="suche" :treffer="gefiltert.length" :gesamt="positionen.length" />
    </div>
    <DataTable
      :value="gefiltert"
      :loading="laedt"
      size="small"
      data-key="isin"
      scrollable
      scroll-height="flex"
      class="lg:min-h-0 lg:flex-1"
    >
      <template #empty><span class="text-muted-color">Keine Positionen.</span></template>
      <Column header="Wertpapier">
        <template #body="{ data }">
          <div class="font-medium">{{ data.name }}</div>
          <small class="block font-mono text-muted-color">{{ data.isin }}</small>
        </template>
      </Column>
      <Column header="Stück" class="w-28 text-right">
        <template #body="{ data }">
          <span class="tabular-nums">{{ stueck.format(Number(data.stueck)) }}</span>
        </template>
      </Column>
      <Column header="Kurs" class="w-36 text-right">
        <template #body="{ data }">
          <div class="tabular-nums">{{ betrag(data.kurs) }} {{ waehrung(data.waehrung) }}</div>
          <small v-if="data.kursdatum" class="block text-muted-color">{{ datum(data.kursdatum) }}</small>
        </template>
      </Column>
      <Column header="Wert" class="w-36 text-right">
        <template #body="{ data }">
          <span class="font-medium tabular-nums">{{ betrag(data.wert) }} €</span>
        </template>
      </Column>
      <Column header="Gewinn/Verlust" class="w-40 text-right">
        <template #body="{ data }">
          <template v-if="data.ergebnis">
            <div class="tabular-nums" :class="farbe(data.ergebnis.betrag)">
              {{ data.ergebnis.betrag > 0 ? "+" : "" }}{{ betrag(data.ergebnis.betrag) }} €
            </div>
            <small class="block text-muted-color tabular-nums">{{ prozent.format(data.ergebnis.prozent) }} %</small>
          </template>
          <span v-else class="text-muted-color">–</span>
        </template>
      </Column>
    </DataTable>
  </section>
</template>
