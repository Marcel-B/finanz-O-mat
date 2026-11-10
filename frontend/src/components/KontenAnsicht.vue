<script setup lang="ts">
// Reiter „Konten“: Salden und Umsätze aller über Enable Banking verbundenen Konten, nur zur Übersicht.
// Mit der Buchhaltung (Kontoumsätze, Belege) hat die Seite nichts zu tun.
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Message from "primevue/message";
import Tag from "primevue/tag";
import { useToast } from "primevue/usetoast";
import { computed, onMounted, ref, watch } from "vue";

import { api, type BankKonto, type KontenUebersicht, type KontoBuchung } from "@/api";
import { betrag, datum, passtZurSuche, suchtext } from "@/format";

import SuchFeld from "./SuchFeld.vue";

const toast = useToast();
const daten = ref<KontenUebersicht | null>(null);
const buchungen = ref<KontoBuchung[]>([]);
const auswahl = ref<string>(""); // "" = alle Konten
const suche = ref("");
const laedt = ref(false);
const ruftAb = ref(false);
const fehler = ref("");

const konten = computed(() => daten.value?.konten ?? []);
const gewaehlt = computed(() => konten.value.find((k) => k.schluessel === auswahl.value) ?? null);
const nachSchluessel = computed(() => Object.fromEntries(konten.value.map((k) => [k.schluessel, k])));

async function laden() {
  laedt.value = true;
  fehler.value = "";
  try {
    daten.value = await api.konten();
    buchungen.value = await api.kontoBuchungen(auswahl.value || undefined);
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    laedt.value = false;
  }
}

watch(auswahl, async () => {
  suche.value = "";
  try {
    buchungen.value = await api.kontoBuchungen(auswahl.value || undefined);
  } catch (e) {
    fehler.value = (e as Error).message;
  }
});

async function abrufen() {
  ruftAb.value = true;
  try {
    const e = await api.kontenAbrufen();
    const probleme = e.konten.filter((k) => k.fehler || k.uebersprungen);
    const neu = e.konten.reduce((n, k) => n + k.neu, 0);
    toast.add({
      severity: probleme.some((k) => k.fehler) ? "warn" : "success",
      summary: neu ? `${neu} neue Umsätze` : "Keine neuen Umsätze",
      detail: probleme.map((k) => `${k.name}: ${k.fehler || k.uebersprungen}`).join(" · ") || undefined,
      life: probleme.length ? 8000 : 3000,
    });
    await laden();
  } catch (e) {
    toast.add({ severity: "error", summary: "Abruf fehlgeschlagen", detail: (e as Error).message, life: 8000 });
  } finally {
    ruftAb.value = false;
  }
}

// Alte Kontoauszüge (CAMT.053, z. B. aus MoneyMoney) in die Übersicht
const dateiFeld = ref<HTMLInputElement | null>(null);
const liestEin = ref(false);

async function einlesen(ereignis: Event) {
  const feld = ereignis.target as HTMLInputElement;
  const dateien = [...(feld.files ?? [])];
  feld.value = "";
  if (!dateien.length) return;
  liestEin.value = true;
  try {
    const zeilen: string[] = [];
    let neu = 0;
    let fehler = false;
    for (const datei of dateien) {
      try {
        for (const a of (await api.kontoauszugEinlesen(datei)).auszuege) {
          if (a.fehler) {
            fehler = true;
            zeilen.push(`${ibanKurz(a.iban)}: ${a.fehler}`);
          } else {
            neu += a.neu;
            const zeitraum = a.von && a.bis ? ` (${datum(a.von)} bis ${datum(a.bis)})` : "";
            zeilen.push(`${a.konto}${zeitraum}: ${a.neu} neu, ${a.vorhanden} schon vorhanden`);
          }
        }
      } catch (e) {
        fehler = true;
        zeilen.push(`${datei.name}: ${(e as Error).message}`);
      }
    }
    toast.add({
      severity: fehler ? "warn" : "success",
      summary: neu ? `${neu} Umsätze eingelesen` : "Keine neuen Umsätze",
      detail: zeilen.join(" · ") || undefined,
      life: fehler ? 10000 : 6000,
    });
    await laden();
  } finally {
    liestEin.value = false;
  }
}

onMounted(laden);

// Uhrzeit in der Zeitzone des Browsers
function zeit(iso: string | null | undefined): string {
  if (!iso) return "nie";
  if (iso.length <= 10) return datum(iso);
  const d = new Date(iso);
  return `${d.toLocaleDateString("de-DE")}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
}

const knoten = (gewaehlt: boolean) => [
  "flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left",
  gewaehlt ? "bg-highlight" : "hover:bg-emphasis",
];

const ibanKurz = (iban: string) => (iban ? `${iban.slice(0, 4)} … ${iban.slice(-4)}` : "");
const ibanLang = (iban: string) => iban.replace(/(.{4})/g, "$1 ").trim();

const summe = computed(() => Object.entries(daten.value?.summen ?? {}));
const letzterAbruf = computed(() =>
  konten.value.map((k) => k.abgerufen).filter(Boolean).sort().at(-1) ?? "",
);
// „9:00, 12:30, 16:00 und 19:30“
const abrufzeiten = computed(() => {
  const z = (daten.value?.zeiten ?? []).map((t) => t.replace(/^0(\d)/, "$1"));
  return z.length > 1 ? `${z.slice(0, -1).join(", ")} und ${z.at(-1)}` : (z[0] ?? "");
});
// Knopf nur, wenn noch mindestens ein Konto einen Abruf frei hat (PSD2: 4 in 24 Stunden)
const abrufFrei = computed(() => konten.value.some((k) => k.abrufe_frei > 0));

function warnung(k: BankKonto): string {
  if (k.fehler) return k.fehler;
  if (k.freigabe_tage !== null && k.freigabe_tage < 0) return "Freigabe abgelaufen";
  if (k.freigabe_tage !== null && k.freigabe_tage <= 14) return `Freigabe läuft in ${k.freigabe_tage} Tagen ab`;
  return "";
}

const gefiltert = computed(() =>
  suche.value.trim()
    ? buchungen.value.filter((b) =>
        passtZurSuche(
          suchtext(b.buchungsdatum, b.partner, b.verwendungszweck, b.iban, b.betrag, b.typ),
          suche.value.trim(),
        ),
      )
    : buchungen.value,
);
</script>

<template>
  <Message v-if="fehler" severity="error" class="mb-4">{{ fehler }}</Message>

  <Message v-if="daten && !daten.eingerichtet && !konten.length" severity="info" class="mb-4">
    Noch kein Kontoabruf eingerichtet. Wie das geht (Enable Banking, <code>ENABLE_BANKING_APP_ID</code> und
    <code>ENABLE_BANKING_SCHLUESSEL</code>), steht im README unter „Automatisch abrufen“.
  </Message>
  <Message v-else-if="daten && !konten.length" severity="info" class="mb-4">
    Noch kein Konto verbunden. Im Container: <code>finanz-o-mat bank verbinden --bank ING</code>
  </Message>

  <div v-if="konten.length" class="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
    <!-- Kontenliste, je Konto ein Knoten -->
    <nav
      class="h-fit rounded-xl border border-surface-200 bg-surface-0 p-2 dark:border-surface-700 dark:bg-surface-900"
      aria-label="Konten"
    >
      <div class="px-2 pt-1 pb-2 text-sm font-semibold text-muted-color">Konten</div>
      <button
        type="button"
        :class="knoten(auswahl === '')"
        @click="auswahl = ''"
      >
        <i class="pi pi-building-columns text-primary" />
        <span class="min-w-0 flex-1 truncate font-medium">Alle Konten</span>
        <span class="text-right font-semibold tabular-nums">
          <span v-for="[w, s] in summe" :key="w" class="block">{{ betrag(s) }} {{ w === "EUR" ? "€" : w }}</span>
        </span>
      </button>
      <button
        v-for="k in konten"
        :key="k.schluessel"
        type="button"
        :class="[knoten(auswahl === k.schluessel), 'pl-5']"
        @click="auswahl = k.schluessel"
      >
        <i class="pi pi-building-columns text-muted-color" />
        <span class="min-w-0 flex-1">
          <span class="flex items-center gap-1 truncate">
            {{ k.name }}
            <i
              v-if="warnung(k)"
              v-tooltip.top="warnung(k)"
              class="pi pi-exclamation-triangle text-xs text-orange-500"
            />
          </span>
          <small class="block truncate text-muted-color">{{ k.bank }} · {{ ibanKurz(k.iban) }}</small>
        </span>
        <span class="text-right tabular-nums" :class="{ 'text-red-600 dark:text-red-400': Number(k.saldo) < 0 }">
          {{ k.saldo === null ? "–" : `${betrag(k.saldo)} ${k.waehrung === "EUR" ? "€" : k.waehrung}` }}
        </span>
      </button>
    </nav>

    <!-- Umsätze des gewählten Kontos -->
    <section
      class="min-w-0 rounded-xl border border-surface-200 bg-surface-0 p-4 dark:border-surface-700 dark:bg-surface-900"
    >
      <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="text-lg font-semibold">
            {{ gewaehlt ? gewaehlt.name : "Alle Konten" }}
            <Tag
              v-if="gewaehlt?.buchhaltung"
              v-tooltip.top="'Die Umsätze dieses Kontos gehen auch in die Buchhaltung (Kontoumsätze)'"
              value="Buchhaltung"
              severity="secondary"
              class="ml-1 align-middle"
            />
          </h2>
          <div v-if="gewaehlt" class="text-sm text-muted-color">
            {{ gewaehlt.bank }}<template v-if="gewaehlt.iban"> · <span class="font-mono">{{ ibanLang(gewaehlt.iban) }}</span></template>
          </div>
          <div class="text-sm text-muted-color">
            <template v-if="gewaehlt">
              Saldo vom {{ zeit(gewaehlt.saldo_stand) }} · Freigabe bis {{ zeit(gewaehlt.freigabe_bis.slice(0, 10)) }}
            </template>
            <template v-else>Zuletzt abgerufen: {{ zeit(letzterAbruf) }}</template>
            <template v-if="abrufzeiten"> · automatisch um {{ abrufzeiten }} Uhr</template>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <div v-if="gewaehlt" class="text-right">
            <div class="text-2xl font-semibold tabular-nums" :class="{ 'text-red-600 dark:text-red-400': Number(gewaehlt.saldo) < 0 }">
              {{ gewaehlt.saldo === null ? "–" : `${betrag(gewaehlt.saldo)} ${gewaehlt.waehrung === "EUR" ? "€" : gewaehlt.waehrung}` }}
            </div>
          </div>
          <input ref="dateiFeld" type="file" accept=".xml,application/xml,text/xml" multiple hidden @change="einlesen" />
          <Button
            v-tooltip.bottom="'Alte Kontoauszüge als CAMT.053 einlesen, z. B. aus MoneyMoney (Ablage → Exportieren)'"
            icon="pi pi-upload"
            label="Auszüge einlesen"
            size="small"
            severity="secondary"
            text
            :loading="liestEin"
            @click="dateiFeld?.click()"
          />
          <Button
            v-tooltip.bottom="
              abrufFrei
                ? `Banken erlauben ${daten?.abrufe_je_tag ?? 4} Abrufe je Konto in 24 Stunden`
                : 'Für heute sind alle Abrufe verbraucht'
            "
            icon="pi pi-refresh"
            label="Jetzt abrufen"
            size="small"
            severity="secondary"
            :loading="ruftAb"
            :disabled="!abrufFrei || !daten?.eingerichtet"
            @click="abrufen"
          />
        </div>
      </div>

      <Message v-if="gewaehlt && warnung(gewaehlt)" severity="warn" class="mb-3">
        {{ warnung(gewaehlt) }}<template v-if="gewaehlt.freigabe_tage !== null && gewaehlt.freigabe_tage <= 14">
          · erneuern im Container: <code>finanz-o-mat bank verbinden --bank {{ gewaehlt.bank }}</code></template
        >
      </Message>

      <div class="mb-2 flex justify-end">
        <SuchFeld v-model="suche" :treffer="gefiltert.length" :gesamt="buchungen.length" />
      </div>
      <DataTable :value="gefiltert" :loading="laedt" size="small" data-key="pruefsumme" scrollable paginator :rows="50">
        <template #empty><span class="text-muted-color">Keine Umsätze.</span></template>
        <Column header="Datum" class="w-28 whitespace-nowrap">
          <template #body="{ data }">{{ datum(data.buchungsdatum) }}</template>
        </Column>
        <Column v-if="!gewaehlt" header="Konto" class="w-36">
          <template #body="{ data }">
            <span class="text-sm">{{ nachSchluessel[data.konto]?.name ?? data.konto }}</span>
          </template>
        </Column>
        <Column header="Empfänger / Verwendungszweck">
          <template #body="{ data }">
            <div class="font-medium">{{ data.partner || "–" }}</div>
            <small v-if="data.verwendungszweck && data.verwendungszweck !== data.partner" class="block text-muted-color">
              {{ data.verwendungszweck }}
            </small>
          </template>
        </Column>
        <Column header="Betrag" class="w-36 text-right">
          <template #body="{ data }">
            <div class="tabular-nums" :class="Number(data.betrag) < 0 ? 'text-red-600 dark:text-red-400' : 'text-green-700 dark:text-green-400'">
              {{ betrag(data.betrag) }} €
            </div>
            <small v-if="data.original_betrag" class="block text-muted-color tabular-nums">
              {{ betrag(data.original_betrag) }} {{ data.original_waehrung }}
            </small>
          </template>
        </Column>
      </DataTable>
    </section>
  </div>
</template>

