<script setup lang="ts">
import AutoComplete from "primevue/autocomplete";
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Tag from "primevue/tag";
import { useToast } from "primevue/usetoast";
import { reactive, ref } from "vue";

import { api, type BelegKurz, type Umsatz } from "@/api";
import { betrag, datum } from "@/format";

import KarteBox from "./KarteBox.vue";

defineProps<{ umsaetze: Umsatz[] }>();
const emit = defineEmits<{ geaendert: [] }>();
const toast = useToast();

const datei = ref<HTMLInputElement | null>(null);
const laedtHoch = ref(false);
const kandidaten = reactive<Record<number, BelegKurz[]>>({});

async function hochladen(event: Event) {
  const eingabe = event.target as HTMLInputElement;
  const f = eingabe.files?.[0];
  eingabe.value = "";
  if (!f) return;
  laedtHoch.value = true;
  try {
    const e = await api.bankImport(await f.text());
    toast.add({
      severity: e.fehler.length ? "warn" : "success",
      summary: "Kontoumsätze eingelesen",
      detail: [`${e.neu} neu, ${e.vorhanden} schon vorhanden, ${e.zugeordnet ?? 0} Belegen zugeordnet`, ...e.fehler].join(" · "),
      life: 6000,
    });
    emit("geaendert");
  } catch (err) {
    fehler(err);
  } finally {
    laedtHoch.value = false;
  }
}

function fehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

// Die Suche läuft auf dem Server über alle Belege; jedes Aufklappen lädt neu, damit gerade
// angelegte oder korrigierte Belege auftauchen.
async function suchen(u: Umsatz, suche: string) {
  try {
    kandidaten[u.id] = await api.kandidaten(u.id, suche.trim());
  } catch (e) {
    kandidaten[u.id] = [];
    fehler(e);
  }
}

const kandidatLabel = (b: BelegKurz) =>
  [
    datum(b.rechnungsdatum),
    b.partner || b.beschreibung || "Beleg",
    b.rechnungsnummer,
    `${betrag(b.zahlbetrag)} ${b.waehrung}`,
    b.zugeordnet && `schon bei Umsatz vom ${datum(b.zugeordnet)}`,
  ]
    .filter(Boolean)
    .join(" · ");

async function aktion(fn: () => Promise<unknown>) {
  try {
    await fn();
    emit("geaendert");
  } catch (e) {
    fehler(e);
  }
}

const zuordnen = (u: Umsatz, belegId: number | null) => aktion(() => api.zuordnen(u.id, belegId));
const ignorieren = (u: Umsatz, regel = false) =>
  aktion(() => api.ignorieren(u.id, regel ? { regel: true, grund: "Partner ohne Beleg" } : {}));
const rueckgaengig = (u: Umsatz) => aktion(() => api.ignorieren(u.id, { ignoriert: false }));
</script>

<template>
  <KarteBox titel="Kontoumsätze">
    <template #aktionen>
      <input ref="datei" type="file" accept=".csv,text/csv" hidden @change="hochladen" />
      <Button label="N26-CSV hochladen" icon="pi pi-upload" severity="secondary" :loading="laedtHoch" @click="datei?.click()" />
    </template>
    <p class="mb-2 text-sm text-muted-color">
      In der N26-App unter Konto → Kontoauszüge den CSV-Export des Monats herunterladen. Umsätze werden automatisch
      passenden Rechnungen zugeordnet; bei Ausgangsrechnungen setzt das das Zahlungsdatum.
    </p>
    <DataTable
      :value="umsaetze"
      size="small"
      data-key="id"
      scrollable
      :row-class="(u: Umsatz) => (!u.beleg && !u.ignoriert ? '!bg-amber-50 dark:!bg-amber-950/40' : '')"
    >
      <template #empty><span class="text-muted-color">Für diesen Monat sind keine Kontoumsätze eingelesen.</span></template>
      <Column header="Datum"><template #body="{ data }">{{ datum(data.buchungsdatum) }}</template></Column>
      <Column header="Partner / Verwendungszweck">
        <template #body="{ data }">
          {{ data.partner || "–" }}
          <div class="text-xs text-muted-color">{{ data.verwendungszweck || data.typ }}</div>
        </template>
      </Column>
      <Column header="Betrag" class="text-right">
        <template #body="{ data }">
          <span class="zahl">{{ Number(data.betrag) > 0 ? "+" : "" }}{{ betrag(data.betrag) }} €</span>
          <div v-if="data.original_waehrung && data.original_waehrung !== 'EUR' && data.original_betrag" class="text-xs text-muted-color">
            {{ betrag(Math.abs(Number(data.original_betrag))) }} {{ data.original_waehrung }}
          </div>
        </template>
      </Column>
      <Column header="Beleg">
        <template #body="{ data }">
          <span v-if="data.beleg">
            <i class="pi pi-check-circle mr-1 text-green-600 dark:text-green-400" />
            {{ data.beleg.partner || data.beleg.beschreibung || "Beleg" }}
            <span v-if="data.beleg.rechnungsnummer" class="text-muted-color">· {{ data.beleg.rechnungsnummer }}</span>
            <div
              v-if="data.kosten_geldverkehr"
              class="text-xs text-muted-color"
              v-tooltip.top="'Kursaufschlag oder Gebühren der Bank. Spielt für die Umsatzsteuer keine Rolle.'"
            >
              {{ Number(data.kosten_geldverkehr) > 0
                ? `${betrag(data.kosten_geldverkehr)} € Kosten Geldverkehr`
                : `${betrag(Math.abs(Number(data.kosten_geldverkehr)))} € Kursgewinn` }}
            </div>
          </span>
          <span v-else-if="data.ignoriert" class="text-sm text-muted-color">
            kein Beleg nötig{{ data.ignoriert_grund ? ` (${data.ignoriert_grund})` : "" }}
          </span>
          <Tag v-else value="Beleg fehlt" severity="danger" icon="pi pi-exclamation-triangle" />
        </template>
      </Column>
      <Column>
        <template #body="{ data }">
          <div class="flex flex-wrap justify-end gap-1">
            <Button
              v-if="data.beleg"
              size="small"
              text
              label="Lösen"
              @click="zuordnen(data, null)"
            />
            <Button
              v-else-if="data.ignoriert"
              size="small"
              text
              label="Rückgängig"
              @click="rueckgaengig(data)"
            />
            <template v-else>
              <AutoComplete
                size="small"
                class="w-60"
                input-class="w-full"
                placeholder="Beleg suchen …"
                dropdown
                :min-length="0"
                :suggestions="kandidaten[data.id] ?? []"
                :option-label="kandidatLabel"
                option-disabled="zugeordnet"
                empty-search-message="Kein offener Beleg gefunden"
                @complete="(e: { query: string }) => suchen(data, e.query)"
                @option-select="(e: { value: BelegKurz }) => zuordnen(data, e.value.id)"
              />
              <Button size="small" severity="secondary" label="Kein Beleg nötig" @click="ignorieren(data)" />
              <Button
                v-if="data.partner"
                size="small"
                severity="secondary"
                text
                label="Partner immer"
                v-tooltip.top="`Alle Umsätze mit ${data.partner} ohne Beleg akzeptieren`"
                @click="ignorieren(data, true)"
              />
            </template>
          </div>
        </template>
      </Column>
    </DataTable>
  </KarteBox>
</template>
