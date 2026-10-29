<script setup lang="ts">
import Button from "primevue/button";
import Checkbox from "primevue/checkbox";
import Dialog from "primevue/dialog";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Textarea from "primevue/textarea";
import ToggleSwitch from "primevue/toggleswitch";
import { useToast } from "primevue/usetoast";
import { onMounted, reactive, ref } from "vue";

import { api, type Auftrag, type TaetigkeitVorlage } from "@/api";

// Was im Tätigkeitsnachweis jeden Monat gleich bleibt: Kopf, Stundensatz, Aufträge und Auswahllisten.
const emit = defineEmits<{ gespeichert: [TaetigkeitVorlage]; importiert: [] }>();
const toast = useToast();

const form = reactive({
  titel: "",
  name: "",
  vorname: "",
  kopf: "",
  stundensatz: null as number | null,
  stunden: 8 as number | null,
  auftraege: [] as Auftrag[],
  rollen: "",
  orte: "",
  leistungsarten: "",
});
const geladen = ref(false);
const speichert = ref(false);
const liestEin = ref(false);
const datei = ref<HTMLInputElement | null>(null);
const listeOffen = ref(false);
const liste = ref("");
const statusUebernehmen = ref(false);
const listeLaeuft = ref(false);

const zeilen = (text: string) => text.split("\n").map((z) => z.trim()).filter(Boolean);

function uebernehmen(v: TaetigkeitVorlage) {
  form.titel = v.titel;
  form.name = v.name;
  form.vorname = v.vorname;
  form.kopf = v.kopf.join("\n");
  form.stundensatz = v.stundensatz == null ? null : Number(v.stundensatz);
  form.stunden = Number(v.stunden);
  form.auftraege = v.auftraege.map((a) => ({ ...a }));
  form.rollen = v.rollen.join("\n");
  form.orte = v.orte.join("\n");
  form.leistungsarten = v.leistungsarten.join("\n");
  geladen.value = true;
}

function fehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

onMounted(async () => {
  try {
    uebernehmen(await api.taetigkeitVorlage());
  } catch (e) {
    fehler(e);
  }
});

async function speichern() {
  speichert.value = true;
  try {
    const v = await api.taetigkeitVorlageSpeichern({
      titel: form.titel,
      name: form.name,
      vorname: form.vorname,
      kopf: zeilen(form.kopf),
      stundensatz: form.stundensatz == null ? null : String(form.stundensatz),
      stunden: String(form.stunden ?? 8),
      auftraege: form.auftraege.filter((a) => a.name.trim()),
      rollen: zeilen(form.rollen),
      orte: zeilen(form.orte),
      leistungsarten: zeilen(form.leistungsarten),
    });
    uebernehmen(v);
    toast.add({ severity: "success", summary: "Vorlage gespeichert", life: 3000 });
    emit("gespeichert", v);
  } catch (e) {
    fehler(e);
  } finally {
    speichert.value = false;
  }
}

// Eingefügte Auftragsliste: nur neue Aufträge kommen dazu, vorhandene bleiben unverändert
async function listeUebernehmen() {
  listeLaeuft.value = true;
  try {
    const e = await api.auftraegeImportieren(liste.value, statusUebernehmen.value);
    const teile = [
      `${e.neu.length} neu${e.inaktiv ? `, davon ${e.inaktiv} inaktiv` : ""}`,
      e.vorhanden.length ? `${e.vorhanden.length} schon vorhanden` : "",
      e.status_geaendert.length ? `Status geändert: ${e.status_geaendert.join(", ")}` : "",
    ];
    toast.add({ severity: "success", summary: "Auftragsliste übernommen", detail: teile.filter(Boolean).join(" · "), life: 8000 });
    uebernehmen(await api.taetigkeitVorlage());
    emit("importiert");
    listeOffen.value = false;
    liste.value = "";
  } catch (err) {
    fehler(err);
  } finally {
    listeLaeuft.value = false;
  }
}

async function einlesen(event: Event) {
  const eingabe = event.target as HTMLInputElement;
  const f = eingabe.files?.[0];
  eingabe.value = "";
  if (!f) return;
  liestEin.value = true;
  try {
    const e = await api.taetigkeitImport(f);
    const teile = [
      e.vorlage.length ? `Vorlage: ${e.vorlage.join(", ")}` : "",
      `${e.eintraege} Einträge aus ${e.monate.length} Monat(en)`,
      e.uebersprungen.length ? `übersprungen, weil schon erfasst: ${e.uebersprungen.join(", ")}` : "",
    ];
    toast.add({ severity: "success", summary: "Excel-Datei übernommen", detail: teile.filter(Boolean).join(" · "), life: 8000 });
    uebernehmen(await api.taetigkeitVorlage());
    emit("importiert");
  } catch (err) {
    fehler(err);
  } finally {
    liestEin.value = false;
  }
}
</script>

<template>
  <form v-if="geladen" class="flex flex-col gap-3" @submit.prevent="speichern">
    <div class="flex flex-wrap items-center gap-2 rounded-lg bg-surface-100 p-3 text-sm dark:bg-surface-800">
      <span class="flex-1">
        Bisherige Excel-Datei übernehmen: liest Kopf, Stundensatz, Aufträge, Auswahllisten und alle Monate ein.
        Monate, die hier schon Einträge haben, bleiben unverändert.
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

    <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="flex flex-col gap-1 text-sm">
        Stundensatz netto
        <InputNumber v-model="form.stundensatz" mode="currency" currency="EUR" locale="de-DE" fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Stunden pro Tag (Vorschlag)
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
        Name
        <InputText v-model="form.name" fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Vorname
        <InputText v-model="form.vorname" fluid />
      </label>
    </div>
    <div class="grid gap-3 sm:grid-cols-2">
      <label class="flex flex-col gap-1 text-sm">
        Überschrift
        <InputText v-model="form.titel" fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Rechts oben (eine Zeile je Eintrag)
        <Textarea v-model="form.kopf" rows="4" auto-resize fluid />
      </label>
    </div>

    <div class="flex flex-col gap-1 text-sm">
      Aufträge
      <div v-for="(a, i) in form.auftraege" :key="i" class="flex items-center gap-2">
        <InputText v-model="a.name" placeholder="Auftrag" class="flex-[2]" :class="{ 'opacity-60': !a.aktiv }" />
        <InputText v-model="a.nummer" placeholder="Auftragsnummer" class="flex-1" :class="{ 'opacity-60': !a.aktiv }" />
        <label class="flex w-20 items-center gap-1 text-xs text-muted-color">
          <ToggleSwitch v-model="a.aktiv" class="scale-75" />
          {{ a.aktiv ? "aktiv" : "inaktiv" }}
        </label>
        <Button
          icon="pi pi-trash"
          severity="secondary"
          text
          aria-label="Auftrag entfernen"
          @click="form.auftraege.splice(i, 1)"
        />
      </div>
      <div>
        <Button
          label="Auftrag hinzufügen"
          icon="pi pi-plus"
          size="small"
          severity="secondary"
          text
          @click="form.auftraege.push({ name: '', nummer: '', aktiv: true })"
        />
        <Button
          label="Auftragsliste einfügen"
          icon="pi pi-clipboard"
          size="small"
          severity="secondary"
          text
          @click="listeOffen = true"
        />
      </div>
    </div>

    <div class="grid gap-3 sm:grid-cols-3">
      <label class="flex flex-col gap-1 text-sm">
        Rollen
        <Textarea v-model="form.rollen" rows="3" auto-resize fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Orte
        <Textarea v-model="form.orte" rows="3" auto-resize fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Leistungsarten
        <Textarea v-model="form.leistungsarten" rows="3" auto-resize fluid />
      </label>
    </div>
    <span class="-mt-1 text-xs text-muted-color">
      Eine Zeile je Eintrag. Der Stundensatz wird beim Erfassen am Eintrag gespeichert, ein neuer Satz ändert also keine
      alten Monate.
    </span>
    <div>
      <Button type="submit" label="Speichern" icon="pi pi-save" :loading="speichert" />
    </div>
  </form>

  <Dialog v-model:visible="listeOffen" header="Auftragsliste einfügen" modal :style="{ width: '44rem' }">
    <div class="flex flex-col gap-3 text-sm">
      <span>
        Liste aus Excel oder dem Kundenportal kopieren und hier einfügen, gern mit Kopfzeile (z. B. Auftrag,
        Auftragsnummer, Status oder Gültig bis). Neue Aufträge kommen dazu, vorhandene behalten Name und Nummer, damit
        erfasste Tätigkeiten ihren Auftrag behalten.
      </span>
      <Textarea
        v-model="liste"
        rows="10"
        fluid
        class="font-mono text-xs"
        placeholder="Auftragsnummer&#9;Auftrag&#9;Status"
      />
      <label class="flex items-center gap-2">
        <Checkbox v-model="statusUebernehmen" binary />
        Status vorhandener Aufträge (aktiv/inaktiv) aus der Liste übernehmen
      </label>
      <span class="text-xs text-muted-color">
        Ungespeicherte Änderungen an der Vorlage gehen dabei verloren, vorher also speichern.
      </span>
    </div>
    <template #footer>
      <Button label="Abbrechen" severity="secondary" text @click="listeOffen = false" />
      <Button
        label="Übernehmen"
        icon="pi pi-check"
        :loading="listeLaeuft"
        :disabled="!liste.trim()"
        @click="listeUebernehmen"
      />
    </template>
  </Dialog>
</template>
