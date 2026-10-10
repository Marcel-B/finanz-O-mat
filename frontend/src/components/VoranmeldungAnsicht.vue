<script setup lang="ts">
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import DatePicker from "primevue/datepicker";
import Message from "primevue/message";
import Tag from "primevue/tag";
import { useToast } from "primevue/usetoast";
import { computed, ref, watch } from "vue";

import { api, type Beleg, type Bericht, type Kennzahl, type Status, type Steuerfall } from "@/api";
import { ausIso, betrag, datum, zeitraumVon } from "@/format";

import BelegDialog from "./BelegDialog.vue";
import KarteBox from "./KarteBox.vue";
import KontoUmsaetze from "./KontoUmsaetze.vue";
import KursTabelle from "./KursTabelle.vue";

const props = defineProps<{ status: Status; steuerfaelle: Steuerfall[] }>();
const toast = useToast();

const monat = ref<Date>(ausIso(`${props.status.frist.zeitraum}-01`) ?? new Date());
const zeitraum = computed(() => zeitraumVon(monat.value));
const bericht = ref<Bericht | null>(null);
const offen = ref<Beleg[]>([]);
const laedt = ref(false);
const liestEin = ref(false);
const dialogBeleg = ref<Beleg | null>(null);
const dialogOffen = ref(false);

const quellen = computed(() =>
  [props.status.verzeichnis && "Verzeichnis", props.status.paperless && "Paperless"].filter(Boolean),
);
const kennzahlen = computed(() => bericht.value?.kennzahlen.filter((k) => k.kz !== "83") ?? []);
const zahllast = computed(() => Number(bericht.value?.zahllast ?? 0));
const euro = (wert: string | null | undefined) => (wert == null ? "–" : `${betrag(wert)} €`);

const stufen: Record<string, "warn" | "info" | "success" | "secondary"> = {
  warnung: "warn",
  sonderfall: "info",
  info: "success",
};

async function laden() {
  laedt.value = true;
  try {
    [bericht.value, offen.value] = await Promise.all([api.bericht(zeitraum.value), api.offen()]);
  } catch (e) {
    meldeFehler(e);
  } finally {
    laedt.value = false;
  }
}

function meldeFehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

watch(zeitraum, laden, { immediate: true });

async function einlesen() {
  liestEin.value = true;
  try {
    const ergebnis = await api.einlesen(zeitraum.value);
    const namen: Record<string, string> = { verzeichnis: "Verzeichnis", paperless: "Paperless", konto: "Konto" };
    const teile: string[] = [];
    const fehler: string[] = [];
    for (const [quelle, e] of Object.entries(ergebnis)) {
      if (quelle !== "keine") {
        teile.push(`${namen[quelle]}: ${e.neu} neu, ${e.vorhanden} vorhanden${e.zugeordnet ? `, ${e.zugeordnet} zugeordnet` : ""}`);
      }
      fehler.push(...e.fehler);
    }
    toast.add({ severity: "success", summary: "Eingelesen", detail: teile.join(" · "), life: 5000 });
    for (const f of fehler) toast.add({ severity: "warn", summary: "Hinweis", detail: f, life: 8000 });
    await laden();
  } catch (e) {
    meldeFehler(e);
  } finally {
    liestEin.value = false;
  }
}

function elsterWert(k: Kennzahl): string {
  const wert = Number(k.betrag);
  return k.betrag_art === "bemessungsgrundlage" ? String(Math.trunc(wert)) : wert.toFixed(2).replace(".", ",");
}

async function kopieren(k: Kennzahl) {
  await navigator.clipboard?.writeText(elsterWert(k));
  toast.add({ severity: "info", summary: `Kz ${k.kz} kopiert`, detail: elsterWert(k), life: 1500 });
}

function bearbeiten(b: Beleg | null) {
  dialogBeleg.value = b;
  dialogOffen.value = true;
}

async function geprueft(b: Beleg) {
  try {
    await api.belegGeprueft(b.id);
    await laden();
  } catch (e) {
    meldeFehler(e);
  }
}

const steuerfallTitel = (wert: string) => props.steuerfaelle.find((s) => s.wert === wert)?.titel ?? wert;
const steuerfallKz = (wert: string) => props.steuerfaelle.find((s) => s.wert === wert)?.kennzahlen ?? "";
</script>

<template>
  <div class="mb-4 flex flex-wrap items-end gap-2">
    <label class="flex flex-col gap-1 text-sm text-muted-color">
      Voranmeldungszeitraum
      <DatePicker v-model="monat" view="month" date-format="MM yy" show-icon :manual-input="false" class="w-52" />
    </label>
    <Button
      label="Rechnungen einlesen"
      icon="pi pi-download"
      :loading="liestEin"
      :disabled="quellen.length === 0"
      v-tooltip.bottom="quellen.length ? `Aus ${quellen.join(' und ')} einlesen` : 'Keine Quelle konfiguriert'"
      @click="einlesen"
    />
    <Button label="Beleg hinzufügen" icon="pi pi-plus" severity="secondary" @click="bearbeiten(null)" />
    <span class="flex-1" />
    <a :href="`/api/export/${zeitraum}/kennzahlen.csv`">
      <Button label="CSV Kennzahlen" icon="pi pi-file-export" severity="secondary" text />
    </a>
    <a :href="`/api/export/${zeitraum}/belege.csv`">
      <Button label="CSV Belege" icon="pi pi-file-export" severity="secondary" text />
    </a>
  </div>

  <div v-if="bericht" class="grid gap-4 lg:grid-cols-5">
    <KarteBox
      class="lg:col-span-3"
      titel="Für ELSTER"
      :untertitel="`${status.versteuerung === 'ist' ? 'Ist' : 'Soll'}-Versteuerung${status.dauerfristverlaengerung ? ', mit Dauerfristverlängerung' : ''}`"
    >
      <p class="mb-3 text-sm text-muted-color">
        Formular „Umsatzsteuer-Voranmeldung“ (USt 1 A). Bemessungsgrundlagen in vollen Euro, Steuerbeträge mit Cent.
        Kennzahl 500 bleibt im Normalfall leer.
      </p>
      <table class="w-full text-left">
        <thead>
          <tr class="border-b border-surface-200 text-xs text-muted-color dark:border-surface-700">
            <th class="py-2 pr-2">Kz</th>
            <th class="py-2 pr-2">Bezeichnung</th>
            <th class="py-2 pr-2 text-right">Betrag</th>
            <th class="py-2" />
          </tr>
        </thead>
        <tbody>
          <tr v-if="kennzahlen.length === 0">
            <td colspan="4" class="py-3 text-sm text-muted-color">
              Keine Werte. Alle Kennzahlen bleiben leer (Nullmeldung).
            </td>
          </tr>
          <tr
            v-for="k in kennzahlen"
            :key="k.id"
            class="border-b border-surface-100 dark:border-surface-800"
            :class="k.ist_steuerzeile ? 'text-sm text-muted-color' : ''"
          >
            <td class="py-2 pr-2 font-bold">{{ k.ist_steuerzeile ? "" : k.kz }}</td>
            <td class="py-2 pr-2">{{ k.titel }}</td>
            <td class="zahl py-2 pr-2 text-right font-semibold">
              {{ betrag(k.betrag, k.betrag_art === "bemessungsgrundlage") }} €
            </td>
            <td class="py-2 text-right">
              <span v-if="k.von_elster_berechnet" class="text-xs text-muted-color">berechnet ELSTER</span>
              <Button v-else size="small" text icon="pi pi-copy" label="Kopieren" @click="kopieren(k)" />
            </td>
          </tr>
        </tbody>
      </table>
      <div class="mt-3 flex flex-wrap items-baseline justify-between gap-2 rounded-lg bg-surface-100 p-3 dark:bg-surface-800">
        <div>
          <strong>Kz 83</strong> {{ zahllast < 0 ? "Überschuss (Erstattung)" : "Zahllast" }}
          <div class="text-sm text-muted-color">
            Umsatzsteuer {{ betrag(bericht.umsatzsteuer) }} € − Vorsteuer {{ betrag(bericht.vorsteuer) }} €
          </div>
        </div>
        <div class="zahl text-3xl font-bold">{{ betrag(bericht.zahllast) }} €</div>
      </div>
    </KarteBox>

    <KarteBox class="lg:col-span-2" titel="Hinweise">
      <div class="flex flex-col gap-2">
        <Message v-if="!bericht.vollstaendig" severity="error">Die Werte sind unvollständig.</Message>
        <Message v-for="(h, i) in bericht.hinweise" :key="i" :severity="stufen[h.stufe]">{{ h.text }}</Message>
        <Message v-if="bericht.vollstaendig && bericht.hinweise.length === 0" severity="success">
          Keine Auffälligkeiten.
        </Message>
      </div>
      <template v-if="bericht.zm.length">
        <h3 class="mt-4 mb-1 font-semibold">Zusammenfassende Meldung (BZSt)</h3>
        <table class="w-full text-left text-sm">
          <tr v-for="z in bericht.zm" :key="z.ustid + z.art" class="border-b border-surface-100 dark:border-surface-800">
            <td class="py-1">{{ z.ustid || "USt-IdNr. fehlt" }}</td>
            <td class="py-1">{{ z.art }}</td>
            <td class="zahl py-1 text-right">{{ betrag(z.betrag, true) }} €</td>
          </tr>
        </table>
      </template>
    </KarteBox>
  </div>

  <KarteBox v-if="bericht" titel="Belege im Zeitraum">
    <DataTable
      :value="bericht.belege"
      :loading="laedt"
      size="small"
      data-key="id"
      row-hover
      scrollable
      :row-class="(b: Beleg) => (b.pruefen ? '!bg-amber-50 dark:!bg-amber-950/40 cursor-pointer' : 'cursor-pointer')"
      @row-click="bearbeiten($event.data)"
    >
      <template #empty><span class="text-muted-color">Keine Belege in diesem Zeitraum.</span></template>
      <Column header="Datum">
        <template #body="{ data }">
          {{ datum(data.rechnungsdatum) }}
          <div v-if="data.zahlungsdatum" class="text-xs text-muted-color">bez. {{ datum(data.zahlungsdatum) }}</div>
        </template>
      </Column>
      <Column header="Art">
        <template #body="{ data }">
          <Tag :value="data.art === 'ausgang' ? 'Ausgang' : 'Eingang'" :severity="data.art === 'ausgang' ? 'info' : 'secondary'" />
        </template>
      </Column>
      <Column header="Partner / Beschreibung">
        <template #body="{ data }">
          {{ data.partner || data.beschreibung || "–" }}
          <Tag v-if="data.pruefen" value="prüfen" severity="warn" class="ml-1" />
          <Tag
            v-if="data.erechnung"
            value="E-Rechnung"
            severity="success"
            icon="pi pi-verified"
            class="ml-1"
            v-tooltip.top="`${data.erechnung}: Beträge exakt aus den Rechnungsdaten gelesen`"
          />
          <div v-if="data.partner && data.beschreibung" class="text-xs text-muted-color">{{ data.beschreibung }}</div>
          <div v-if="data.fehler" class="text-xs text-red-600 dark:text-red-400">{{ data.fehler }}</div>
        </template>
      </Column>
      <Column header="Steuerfall">
        <template #body="{ data }">
          {{ steuerfallTitel(data.steuerfall) }}
          <div class="text-xs text-muted-color">Kz {{ steuerfallKz(data.steuerfall) }}</div>
        </template>
      </Column>
      <Column header="Netto" class="text-right">
        <template #body="{ data }">
          <span class="zahl">{{ euro(data.netto_eur) }}</span>
          <div v-if="data.waehrung !== 'EUR'" class="text-xs whitespace-nowrap text-muted-color">
            {{ betrag(data.netto) }} {{ data.waehrung }}<template v-if="data.kurs_verwendet"> · Kurs {{ data.kurs_verwendet }}</template>
          </div>
        </template>
      </Column>
      <Column header="Steuer" class="text-right">
        <template #body="{ data }">
          <span class="zahl">{{ euro(data.steuer_eur) }}</span>
          <div class="text-xs whitespace-nowrap text-muted-color">
            {{ betrag(data.steuersatz) }} %
            <span
              v-if="data.steuer_auf_rechnung === false && Number(data.steuer_eur) > 0"
              v-tooltip.top="'Steuer schuldest du selbst (Reverse Charge); sie steht nicht auf der Rechnung und ist nicht im Brutto enthalten.'"
            >· selbst geschuldet</span>
          </div>
        </template>
      </Column>
      <Column header="Brutto" class="text-right">
        <template #body="{ data }"><span class="zahl">{{ euro(data.brutto_eur) }}</span></template>
      </Column>
      <Column>
        <template #body="{ data }">
          <div class="flex gap-1" @click.stop>
            <Button v-if="data.pruefen" size="small" label="OK" severity="secondary" @click="geprueft(data)" />
            <a v-if="data.quelle === 'paperless'" :href="data.quelle_link" target="_blank" rel="noopener">
              <Button size="small" text icon="pi pi-external-link" aria-label="In Paperless öffnen" />
            </a>
            <a v-if="data.quelle === 'verzeichnis'" :href="`/api/belege/${data.id}/datei`" target="_blank">
              <Button size="small" text icon="pi pi-file" aria-label="Datei öffnen" />
            </a>
          </div>
        </template>
      </Column>
    </DataTable>
  </KarteBox>

  <KontoUmsaetze v-if="bericht" :umsaetze="bericht.umsaetze" @geaendert="laden" />

  <KarteBox v-if="offen.length" titel="Belege ohne Zeitraum">
    <p class="mb-2 text-sm text-muted-color">
      Ohne Rechnungsdatum oder, bei Ist-Versteuerung, noch nicht bezahlt. Sie zählen erst, wenn das Datum eingetragen ist.
    </p>
    <DataTable :value="offen" size="small" data-key="id" row-hover class="cursor-pointer" @row-click="bearbeiten($event.data)">
      <Column header="Rechnungsdatum"><template #body="{ data }">{{ datum(data.rechnungsdatum) }}</template></Column>
      <Column header="Art"><template #body="{ data }">{{ data.art === "ausgang" ? "Ausgang" : "Eingang" }}</template></Column>
      <Column header="Partner / Beschreibung">
        <template #body="{ data }">{{ data.partner || data.beschreibung || "–" }}</template>
      </Column>
      <Column header="Netto" class="text-right">
        <template #body="{ data }"><span class="zahl">{{ betrag(data.netto) }} {{ data.waehrung }}</span></template>
      </Column>
    </DataTable>
  </KarteBox>

  <KursTabelle @geaendert="laden" />

  <BelegDialog
    v-model:visible="dialogOffen"
    :beleg="dialogBeleg"
    :steuerfaelle="steuerfaelle"
    :zeitraum="zeitraum"
    @gespeichert="laden"
  />
</template>
