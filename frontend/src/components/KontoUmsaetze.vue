<script setup lang="ts">
import AutoComplete from "primevue/autocomplete";
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import Tag from "primevue/tag";
import { useToast } from "primevue/usetoast";
import { computed, reactive, ref } from "vue";

import { api, type BelegKurz, type Umsatz } from "@/api";
import { betrag, datum, passtZurSuche, suchtext } from "@/format";

import KarteBox from "./KarteBox.vue";
import SuchFeld from "./SuchFeld.vue";

const props = defineProps<{ umsaetze: Umsatz[] }>();
const emit = defineEmits<{ geaendert: []; oeffnen: [belegId: number] }>();
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

// Liegt der Beleg in einem anderen Monat, sucht man ihn sonst in der Belegliste vergeblich.
function belegDatum(u: Umsatz): string {
  const d = u.beleg?.rechnungsdatum;
  if (!d) return "ohne Rechnungsdatum";
  return d.slice(0, 7) === u.buchungsdatum.slice(0, 7) ? "" : `Rechnung vom ${datum(d)}`;
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

const suche = ref("");
const gefunden = computed(() =>
  props.umsaetze.filter((u) =>
    passtZurSuche(
      suchtext(
        u.buchungsdatum, u.partner, u.verwendungszweck, u.typ, u.betrag, u.original_betrag, u.original_waehrung,
        u.ignoriert_grund, u.beleg?.partner, u.beleg?.beschreibung, u.beleg?.rechnungsnummer,
        !u.beleg && !u.ignoriert && "ohne Beleg",
      ),
      suche.value,
    ),
  ),
);

const zuordnen = (u: Umsatz, belegId: number | null) => aktion(() => api.zuordnen(u.id, belegId));
const ignorieren = (u: Umsatz, regel = false) =>
  aktion(() => api.ignorieren(u.id, regel ? { regel: true, grund: "Partner ohne Beleg" } : {}));
const rueckgaengig = (u: Umsatz) => aktion(() => api.ignorieren(u.id, { ignoriert: false }));

// Fehlbuchung oder private Ausgabe vom Geschäftskonto: zählt nicht zur Steuer, steht aber im Monats-ZIP.
const FEHLBUCHUNG = "Fehlbuchung/privat";
const fehlbuchung = ref<Umsatz | null>(null);
const notiz = ref("");
const istFehlbuchung = (u: Umsatz) => u.ignoriert && u.ignoriert_grund.startsWith(FEHLBUCHUNG);
const fehlbuchungNotiz = (u: Umsatz) => u.ignoriert_grund.slice(FEHLBUCHUNG.length).replace(/^[\s:]+/, "");

function fehlbuchungMarkieren(u: Umsatz) {
  fehlbuchung.value = u;
  notiz.value = "";
}

async function fehlbuchungSpeichern() {
  const u = fehlbuchung.value;
  if (!u) return;
  const grund = notiz.value.trim() ? `${FEHLBUCHUNG}: ${notiz.value.trim()}` : FEHLBUCHUNG;
  fehlbuchung.value = null;
  await aktion(() => api.ignorieren(u.id, { grund }));
}
</script>

<template>
  <KarteBox titel="Kontoumsätze">
    <template #aktionen>
      <SuchFeld v-model="suche" :treffer="gefunden.length" :gesamt="umsaetze.length" />
      <input ref="datei" type="file" accept=".csv,text/csv" hidden @change="hochladen" />
      <Button label="N26-CSV hochladen" icon="pi pi-upload" severity="secondary" :loading="laedtHoch" @click="datei?.click()" />
    </template>
    <p class="mb-2 text-sm text-muted-color">
      In der N26-App unter Konto → Kontoauszüge den CSV-Export des Monats herunterladen. Umsätze werden automatisch
      passenden Rechnungen zugeordnet; bei Ausgangsrechnungen setzt das das Zahlungsdatum.
    </p>
    <DataTable
      :value="gefunden"
      size="small"
      data-key="id"
      scrollable
      :row-class="(u: Umsatz) => (!u.beleg && !u.ignoriert ? '!bg-amber-50 dark:!bg-amber-950/40' : '')"
    >
      <template #empty>
        <span class="text-muted-color">
          {{ suche.trim() ? "Keine Kontoumsätze passen zur Suche." : "Für diesen Monat sind keine Kontoumsätze eingelesen." }}
        </span>
      </template>
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
            <a
              href="#"
              class="hover:underline"
              v-tooltip.top="'Beleg öffnen'"
              @click.prevent="emit('oeffnen', data.beleg.id)"
            >
              {{ data.beleg.partner || data.beleg.beschreibung || "Beleg" }}
              <span v-if="data.beleg.rechnungsnummer" class="text-muted-color">· {{ data.beleg.rechnungsnummer }}</span>
            </a>
            <div v-if="belegDatum(data)" class="text-xs text-amber-700 dark:text-amber-400">{{ belegDatum(data) }}</div>
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
          <span v-else-if="istFehlbuchung(data)" class="text-sm">
            <Tag value="Fehlbuchung/privat" severity="warn" />
            <span v-if="fehlbuchungNotiz(data)" class="ml-1 text-muted-color">{{ fehlbuchungNotiz(data) }}</span>
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
              >
                <template #option="{ option }">
                  <span>{{ kandidatLabel(option) }}</span>
                  <span
                    v-for="n in option.nummern ?? []"
                    :key="n.nummer"
                    class="ml-2 inline-flex items-center gap-1 rounded px-1.5 text-xs"
                    :class="n.eindeutig ? 'bg-primary text-primary-contrast' : 'bg-surface-200 text-surface-700 dark:bg-surface-700 dark:text-surface-100'"
                    :title="
                      n.eindeutig
                        ? `Die Nummer ${n.nummer} aus dem Verwendungszweck steht nur in diesem Beleg`
                        : `Die Nummer ${n.nummer} aus dem Verwendungszweck steht in mehreren Belegen (z. B. Kunden- oder Vertragsnummer)`
                    "
                    ><i class="pi pi-hashtag text-[0.6rem]" />{{ n.nummer }}</span
                  >
                </template>
              </AutoComplete>
              <Button size="small" severity="secondary" label="Kein Beleg nötig" @click="ignorieren(data)" />
              <Button
                size="small"
                severity="secondary"
                text
                label="Fehlbuchung"
                v-tooltip.top="'Privat oder versehentlich gebucht: zählt nicht zur Steuer, steht im ZIP für die Steuerberatung'"
                @click="fehlbuchungMarkieren(data)"
              />
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
    <Dialog
      :visible="!!fehlbuchung"
      modal
      header="Als Fehlbuchung / privat markieren"
      class="w-[min(480px,calc(100vw-2rem))]"
      @update:visible="(v: boolean) => !v && (fehlbuchung = null)"
    >
      <form class="flex flex-col gap-3" @submit.prevent="fehlbuchungSpeichern">
        <p class="text-sm text-muted-color">
          {{ fehlbuchung?.partner || fehlbuchung?.verwendungszweck }} · {{ betrag(fehlbuchung?.betrag ?? "0") }} €.
          Der Umsatz zählt nicht zur Umsatzsteuer und erscheint im ZIP für die Steuerberatung in der INFO.txt.
        </p>
        <label class="flex flex-col gap-1 text-sm">
          Notiz für die Steuerberatung (optional)
          <InputText v-model="notiz" fluid autofocus placeholder="z. B. privater Einkauf, Karte verwechselt" />
        </label>
        <div class="flex justify-end gap-2">
          <Button label="Abbrechen" severity="secondary" text @click="fehlbuchung = null" />
          <Button type="submit" label="Markieren" icon="pi pi-check" />
        </div>
      </form>
    </Dialog>
  </KarteBox>
</template>
