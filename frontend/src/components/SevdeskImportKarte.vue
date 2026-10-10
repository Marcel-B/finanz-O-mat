<script setup lang="ts">
// Umzug aus sevDesk: Export-Ordner (oder die ZIPs) hineinziehen, Probelauf ansehen, übernehmen (ustva/sevdesk_web.py)
import Button from "primevue/button";
import DatePicker from "primevue/datepicker";
import Message from "primevue/message";
import ProgressBar from "primevue/progressbar";
import { useConfirm } from "primevue/useconfirm";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import { api, type SevdeskErgebnis, type SevdeskStatus } from "@/api";
import { betrag, zeitraumVon } from "@/format";
import KarteBox from "./KarteBox.vue";

const confirm = useConfirm();

const status = ref<SevdeskStatus | null>(null);
const fehler = ref("");
const ziehtDrueber = ref(false);
const hochladen = ref<{ fertig: number; gesamt: number } | null>(null);
const probe = ref<SevdeskErgebnis | null>(null);
const probeLaeuft = ref(false);
const bis = ref<Date | null>(null);
const ordnerWahl = ref<HTMLInputElement | null>(null);
const dateiWahl = ref<HTMLInputElement | null>(null);
let timer: number | undefined;

const BRAUCHBAR = /\.(csv|zip|pdf|png|jpe?g|gif|tiff?|webp|xml)$/i;
const vorhanden = computed(() => !!status.value && (status.value.csv.length > 0 || status.value.dateien > 0));
const ergebnis = computed(() => status.value?.ergebnis ?? null);
const anzeige = computed(() => probe.value ?? ergebnis.value); // neuer Probelauf ersetzt das letzte Ergebnis
const bisText = computed(() => (bis.value ? zeitraumVon(bis.value) : null));

async function laden() {
  try {
    status.value = await api.sevdesk();
  } catch (e) {
    fehler.value = (e as Error).message;
  }
  clearTimeout(timer);
  if (status.value?.laeuft) timer = window.setTimeout(laden, 2000);
}

onMounted(async () => {
  await laden();
  if (vorhanden.value && !status.value?.laeuft && !ergebnis.value) await probelauf();
});
onUnmounted(() => clearTimeout(timer));
watch(bisText, () => vorhanden.value && probelauf());

// Ordner per Drag & Drop: Verzeichnisse rekursiv auslesen
function eintraegeLesen(eintrag: FileSystemEntry, pfad = ""): Promise<{ datei: File; pfad: string }[]> {
  if (eintrag.isFile) {
    return new Promise((ok, fehl) =>
      (eintrag as FileSystemFileEntry).file((d) => ok([{ datei: d, pfad: pfad + d.name }]), fehl),
    );
  }
  const leser = (eintrag as FileSystemDirectoryEntry).createReader();
  return new Promise((ok, fehl) => {
    const alle: FileSystemEntry[] = [];
    const weiter = () =>
      leser.readEntries(async (teil) => {
        if (teil.length) {
          alle.push(...teil);
          weiter(); // readEntries liefert höchstens 100 Einträge auf einmal
        } else {
          const listen = await Promise.all(alle.map((e) => eintraegeLesen(e, `${pfad}${eintrag.name}/`)));
          ok(listen.flat());
        }
      }, fehl);
    weiter();
  });
}

async function fallengelassen(ev: DragEvent) {
  ziehtDrueber.value = false;
  const items = [...(ev.dataTransfer?.items ?? [])];
  const eintraege = items.map((i) => i.webkitGetAsEntry()).filter((e): e is FileSystemEntry => !!e);
  const dateien = eintraege.length
    ? (await Promise.all(eintraege.map((e) => eintraegeLesen(e)))).flat()
    : [...(ev.dataTransfer?.files ?? [])].map((d) => ({ datei: d, pfad: d.name }));
  await hochladenAlle(dateien);
}

async function gewaehlt(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const dateien = [...(input.files ?? [])].map((d) => ({ datei: d, pfad: d.webkitRelativePath || d.name }));
  input.value = "";
  await hochladenAlle(dateien);
}

async function hochladenAlle(liste: { datei: File; pfad: string }[]) {
  const dateien = liste.filter((d) => BRAUCHBAR.test(d.datei.name) && !d.datei.name.startsWith("."));
  if (!dateien.some((d) => /\.(csv|zip)$/i.test(d.datei.name))) {
    fehler.value = "Keine sevDesk-Export-Dateien gefunden. Erwartet werden voucher.csv, invoices.csv und die Belege.";
    return;
  }
  fehler.value = "";
  probe.value = null;
  hochladen.value = { fertig: 0, gesamt: dateien.length };
  try {
    await api.sevdeskLeeren();
    const warteschlange = [...dateien];
    // vier gleichzeitig: schnell genug, ohne den Container zu überlasten
    await Promise.all(
      Array.from({ length: 4 }, async () => {
        for (let d = warteschlange.shift(); d; d = warteschlange.shift()) {
          await api.sevdeskHochladen(d.pfad, d.datei);
          hochladen.value!.fertig++;
        }
      }),
    );
    await laden();
    await probelauf();
  } catch (e) {
    fehler.value = `Hochladen fehlgeschlagen: ${(e as Error).message}`;
  } finally {
    hochladen.value = null;
  }
}

async function probelauf() {
  probeLaeuft.value = true;
  fehler.value = "";
  try {
    probe.value = await api.sevdeskProbelauf({ bis: bisText.value });
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    probeLaeuft.value = false;
  }
}

function uebernehmen() {
  const p = probe.value;
  confirm.require({
    header: "Aus sevDesk übernehmen",
    message:
      `${p?.neu ?? 0} Belege und Rechnungen werden angelegt` +
      (status.value?.paperless ? " und ihre Dateien nach Paperless geladen" : "") +
      ". Ein zweiter Lauf legt nichts doppelt an.",
    acceptLabel: "Übernehmen",
    rejectLabel: "Abbrechen",
    accept: async () => {
      try {
        status.value = await api.sevdeskImportieren({ bis: bisText.value });
        probe.value = null;
        timer = window.setTimeout(laden, 1000);
      } catch (e) {
        fehler.value = (e as Error).message;
      }
    },
  });
}

const abweichungen = (e: SevdeskErgebnis) => e.vergleich.filter((z) => z.erwartet != null && !z.passt).length;
const mitWert = (e: SevdeskErgebnis) => e.vergleich.filter((z) => z.erwartet != null).length;
</script>

<template>
  <KarteBox titel="Umzug aus sevDesk" untertitel="einmalig">
    <p class="mb-3 text-sm text-muted-color">
      Den entpackten Export-Ordner aus sevDesk hier hineinziehen (voucher.csv, invoices.csv, contacts.csv und die
      Ordner mit den Belegen und Rechnungen) oder die CSV- und ZIP-Dateien direkt. Zuerst kommt ein Probelauf, der nichts
      speichert.
    </p>

    <div
      class="mb-3 flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 text-center transition-colors"
      :class="ziehtDrueber ? 'border-primary bg-primary-50 dark:bg-primary-950/30' : 'border-surface-300 dark:border-surface-600'"
      @dragover.prevent="ziehtDrueber = true"
      @dragleave.prevent="ziehtDrueber = false"
      @drop.prevent="fallengelassen"
    >
      <i class="pi pi-folder-open text-3xl text-muted-color" />
      <div class="text-sm">sevDesk-Ordner hierher ziehen</div>
      <div class="flex flex-wrap justify-center gap-2">
        <Button label="Ordner wählen" icon="pi pi-folder" size="small" severity="secondary" outlined :disabled="!!hochladen || status?.laeuft" @click="ordnerWahl?.click()" />
        <Button label="Dateien wählen" icon="pi pi-file" size="small" severity="secondary" outlined :disabled="!!hochladen || status?.laeuft" @click="dateiWahl?.click()" />
      </div>
      <input ref="ordnerWahl" type="file" class="hidden" webkitdirectory multiple @change="gewaehlt" />
      <input ref="dateiWahl" type="file" class="hidden" multiple accept=".csv,.zip,.pdf,.png,.jpg,.jpeg" @change="gewaehlt" />
    </div>

    <div v-if="hochladen" class="mb-3">
      <div class="mb-1 text-sm">Lade {{ hochladen.fertig }} von {{ hochladen.gesamt }} Dateien hoch …</div>
      <ProgressBar :value="Math.round((hochladen.fertig / hochladen.gesamt) * 100)" />
    </div>
    <Message v-if="fehler" severity="error" class="mb-3">{{ fehler }}</Message>

    <div v-if="status?.laeuft" class="mb-3">
      <div class="mb-1 text-sm">Übernahme läuft: {{ status.meldung }}</div>
      <ProgressBar mode="indeterminate" style="height: 6px" />
    </div>
    <Message v-else-if="status?.fehler" severity="error" class="mb-3">Übernahme abgebrochen: {{ status.fehler }}</Message>

    <template v-if="vorhanden && !hochladen && !status?.laeuft">
      <div class="mb-3 text-sm text-muted-color">
        Hochgeladen: {{ status!.csv.join(", ") || "keine CSV" }} und {{ status!.dateien }} Belegdateien.
      </div>
      <div class="mb-3 flex flex-wrap items-end gap-2">
        <label class="flex flex-col gap-1 text-sm text-muted-color">
          Nur bis Monat (optional)
          <DatePicker v-model="bis" view="month" date-format="MM yy" show-clear placeholder="alle Monate" class="w-48" />
        </label>
        <span class="max-w-md text-xs text-muted-color">
          Monate, die du schon mit finanz-O-mat gemacht hast, hier ausklammern. Gleiche Rechnungen erkennt die App aber
          auch so.
        </span>
      </div>
    </template>

    <div v-if="probeLaeuft" class="text-sm text-muted-color"><i class="pi pi-spin pi-spinner mr-2" />Probelauf …</div>

    <template v-if="anzeige && !hochladen && !status?.laeuft">
      <div v-for="e in [anzeige]" :key="String(e.trocken)" class="mb-3 rounded-lg bg-surface-50 p-3 text-sm dark:bg-surface-800">
        <div class="mb-2 font-semibold">
          {{ e.trocken ? "Probelauf" : "Übernommen" }}:
          {{ e.neu }} Belege und Rechnungen {{ e.trocken ? "würden übernommen" : "angelegt" }}
          <span v-if="e.vorhanden" class="font-normal text-muted-color">· {{ e.vorhanden }} schon da</span>
        </div>
        <ul class="list-inside list-disc space-y-0.5">
          <li v-if="!e.trocken && status?.paperless">
            {{ e.hochgeladen }} Dateien nach Paperless geladen
            <strong v-if="e.wartet">, {{ e.wartet }} noch in Verarbeitung: später erneut „Übernehmen“ klicken</strong>
          </li>
          <li v-if="e.trocken && !status?.paperless" class="text-amber-700 dark:text-amber-400">
            Paperless ist nicht eingerichtet: nur die Buchungen werden übernommen, die Dateien bleiben im Archivordner.
          </li>
          <li>Adressbuch: {{ e.kontakte }} Kontakte aus sevDesk</li>
          <li v-for="u in e.uebersprungen" :key="u.grund">Nicht übernommen: {{ u.anzahl }} × {{ u.grund }}</li>
          <li v-if="e.doppelt.length">{{ e.doppelt.length }} schon in finanz-O-mat, nicht doppelt angelegt</li>
          <li v-if="e.ohne_datei.length">{{ e.ohne_datei.length }} ohne Datei (nur Buchung, z. B. Lohn)</li>
          <li v-if="mitWert(e)">
            Zahllast: {{ mitWert(e) - abweichungen(e) }} von {{ mitWert(e) }} abgegebenen Zeiträumen passen auf den Euro
          </li>
        </ul>
        <Message v-for="f in e.fehler" :key="f" severity="error" class="mt-2">{{ f }}</Message>
        <details v-if="e.pruefen.length" class="mt-2">
          <summary class="cursor-pointer">{{ e.pruefen.length }} Hinweise an Belegen</summary>
          <ul class="mt-1 max-h-60 list-inside list-disc overflow-auto text-xs text-muted-color">
            <li v-for="h in e.pruefen" :key="h">{{ h }}</li>
          </ul>
        </details>
        <details v-if="e.vergleich.length" class="mt-2">
          <summary class="cursor-pointer">Zahllast je Monat</summary>
          <table class="mt-1 text-xs">
            <thead>
              <tr class="text-muted-color">
                <th class="pr-4 text-left">Zeitraum</th><th class="pr-4 text-right">finanz-O-mat</th>
                <th class="pr-4 text-right">abgegeben</th><th class="text-right">Differenz</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="z in e.vergleich" :key="z.zeitraum" :class="z.passt ? '' : 'text-amber-700 dark:text-amber-400'">
                <td class="pr-4">{{ z.zeitraum }}</td>
                <td class="zahl pr-4 text-right">{{ betrag(z.zahllast) }}</td>
                <td class="zahl pr-4 text-right">{{ z.erwartet == null ? "" : betrag(z.erwartet) }}</td>
                <td class="zahl text-right">{{ z.differenz == null ? "" : betrag(z.differenz) }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </div>
    </template>

    <div v-if="vorhanden && !hochladen && !status?.laeuft" class="flex flex-wrap gap-2">
      <Button
        :label="probe || !ergebnis ? 'Übernehmen' : 'Erneut übernehmen'"
        icon="pi pi-check"
        :disabled="probeLaeuft || !anzeige"
        v-tooltip.top="ergebnis?.wartet ? 'Schließt ab, was Paperless noch verarbeitet hat' : ''"
        @click="uebernehmen"
      />
      <Button label="Probelauf wiederholen" icon="pi pi-refresh" severity="secondary" text :loading="probeLaeuft" @click="probelauf" />
    </div>
  </KarteBox>
</template>
