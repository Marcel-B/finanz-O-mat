<script setup lang="ts">
import Button from "primevue/button";
import DatePicker from "primevue/datepicker";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Panel from "primevue/panel";
import { useToast } from "primevue/usetoast";
import { onMounted, ref } from "vue";

import { api, type Kurs } from "@/api";
import { zeitraumVon } from "@/format";

const emit = defineEmits<{ geaendert: [] }>();
const toast = useToast();

const kurse = ref<Kurs[]>([]);
const waehrung = ref("USD");
const monat = ref<Date | null>(null);
const kurs = ref<number | null>(null);

async function laden() {
  kurse.value = await api.kurse();
}
onMounted(laden);

async function speichern() {
  if (!monat.value || !kurs.value || !waehrung.value) return;
  try {
    kurse.value = await api.kursSpeichern({
      waehrung: waehrung.value.trim().toUpperCase(),
      zeitraum: zeitraumVon(monat.value),
      kurs: String(kurs.value),
      quelle: "BMF",
    });
    kurs.value = null;
    emit("geaendert");
  } catch (e) {
    toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
  }
}

async function loeschen(k: Kurs) {
  await api.kursLoeschen(k.waehrung, k.zeitraum);
  await laden();
  emit("geaendert");
}
</script>

<template>
  <Panel header="Umrechnungskurse" toggleable collapsed class="mb-4">
    <p class="mb-3 text-sm text-muted-color">
      Einheiten Fremdwährung je 1 EUR. Maßgeblich sind die monatlichen Umsatzsteuer-Umrechnungskurse des BMF. Ohne
      Eintrag nutzt die App das Monatsmittel der EZB-Referenzkurse.
    </p>
    <form class="mb-3 flex flex-wrap items-end gap-2" @submit.prevent="speichern">
      <InputText v-model="waehrung" maxlength="3" class="w-20" aria-label="Währung" />
      <DatePicker v-model="monat" view="month" date-format="mm/yy" placeholder="Monat" class="w-36" />
      <InputNumber v-model="kurs" locale="de-DE" :min-fraction-digits="4" :max-fraction-digits="6" placeholder="1,1700" />
      <Button type="submit" label="Speichern" icon="pi pi-save" severity="secondary" />
    </form>
    <table class="w-full text-left text-sm">
      <thead>
        <tr class="text-xs text-muted-color">
          <th class="py-1">Monat</th><th class="py-1">Währung</th><th class="py-1 text-right">Kurs</th><th class="py-1">Quelle</th><th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="k in kurse" :key="k.waehrung + k.zeitraum" class="border-t border-surface-100 dark:border-surface-800">
          <td class="py-1">{{ k.zeitraum }}</td>
          <td class="py-1">{{ k.waehrung }}</td>
          <td class="zahl py-1 text-right">{{ k.kurs.replace(".", ",") }}</td>
          <td class="py-1">{{ k.quelle }}</td>
          <td class="py-1 text-right">
            <Button size="small" text severity="danger" icon="pi pi-times" aria-label="Entfernen" @click="loeschen(k)" />
          </td>
        </tr>
      </tbody>
    </table>
  </Panel>
</template>
