<script setup lang="ts">
import Button from "primevue/button";
import DatePicker from "primevue/datepicker";
import Dialog from "primevue/dialog";
import InputNumber from "primevue/inputnumber";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Select from "primevue/select";
import SelectButton from "primevue/selectbutton";
import { useConfirm } from "primevue/useconfirm";
import { computed, reactive, ref, watch } from "vue";

import { api, type Art, type Beleg, type BelegEingabe, type Steuerfall } from "@/api";
import { alsIso, ausIso } from "@/format";

const props = defineProps<{ beleg: Beleg | null; steuerfaelle: Steuerfall[]; zeitraum: string }>();
const sichtbar = defineModel<boolean>("visible", { required: true });
const emit = defineEmits<{ gespeichert: [] }>();
const confirm = useConfirm();

interface Formular {
  art: Art;
  steuerfall: string;
  rechnungsdatum: Date | null;
  zahlungsdatum: Date | null;
  partner: string;
  partner_ustid: string;
  rechnungsnummer: string;
  beschreibung: string;
  waehrung: string;
  netto: number | null;
  steuersatz: number | null;
  steuer: number | null;
  kurs: number | null;
}

const form = reactive<Formular>(leer());
const fehler = ref("");
const speichert = ref(false);
const liestNeu = ref(false);
const neuLesbar = computed(() => !!props.beleg?.pruefen && ["verzeichnis", "paperless"].includes(props.beleg.quelle));

async function neuEinlesen() {
  const b = props.beleg;
  if (!b) return;
  liestNeu.value = true;
  fehler.value = "";
  try {
    const r = await api.belegeNeuEinlesen([b.id]);
    if (r.fehler.length) {
      fehler.value = r.fehler.join(" ");
      return;
    }
    sichtbar.value = false;
    emit("gespeichert");
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    liestNeu.value = false;
  }
}

function leer(): Formular {
  return {
    art: "eingang", steuerfall: "vorsteuer_inland", rechnungsdatum: ausIso(`${props.zeitraum}-01`),
    zahlungsdatum: null, partner: "", partner_ustid: "", rechnungsnummer: "", beschreibung: "", waehrung: "EUR",
    netto: null, steuersatz: 19, steuer: null, kurs: null,
  };
}

const zahl = (v: string | null) => (v == null || v === "" ? null : Number(v));

watch(sichtbar, (offen) => {
  if (!offen) return;
  fehler.value = "";
  const b = props.beleg;
  Object.assign(form, b ? {
    art: b.art, steuerfall: b.steuerfall, rechnungsdatum: ausIso(b.rechnungsdatum), zahlungsdatum: ausIso(b.zahlungsdatum),
    partner: b.partner, partner_ustid: b.partner_ustid, rechnungsnummer: b.rechnungsnummer, beschreibung: b.beschreibung,
    waehrung: b.waehrung, netto: zahl(b.netto), steuersatz: zahl(b.steuersatz), steuer: zahl(b.steuer), kurs: zahl(b.kurs),
  } : leer());
});

const arten = [
  { label: "Ausgang", value: "ausgang" },
  { label: "Eingang", value: "eingang" },
];
const faelle = computed(() =>
  props.steuerfaelle
    .filter((s) => s.art === form.art || s.art === null)
    .map((s) => ({ label: `${s.titel} (Kz ${s.kennzahlen})`, value: s.wert })),
);
watch(() => form.art, () => {
  if (!faelle.value.some((f) => f.value === form.steuerfall)) form.steuerfall = faelle.value[0]?.value ?? "";
});

const text = (v: number | null) => (v == null ? null : String(v));

async function speichern() {
  if (form.netto == null) {
    fehler.value = "Bitte einen Nettobetrag eintragen.";
    return;
  }
  const daten: BelegEingabe = {
    art: form.art,
    steuerfall: form.steuerfall,
    rechnungsdatum: alsIso(form.rechnungsdatum),
    zahlungsdatum: alsIso(form.zahlungsdatum),
    partner: form.partner,
    partner_ustid: form.partner_ustid.replace(/\s/g, "").toUpperCase(),
    rechnungsnummer: form.rechnungsnummer,
    beschreibung: form.beschreibung,
    waehrung: form.waehrung.trim().toUpperCase(),
    netto: String(form.netto),
    steuersatz: text(form.steuersatz) ?? "0",
    steuer: text(form.steuer),
    kurs: text(form.kurs),
    pruefen: false,
  };
  speichert.value = true;
  try {
    if (props.beleg) await api.belegAendern(props.beleg.id, daten);
    else await api.belegAnlegen(daten);
    sichtbar.value = false;
    emit("gespeichert");
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    speichert.value = false;
  }
}

function loeschen() {
  const b = props.beleg;
  if (!b) return;
  confirm.require({
    header: "Beleg löschen",
    message: "Beleg wirklich löschen?",
    icon: "pi pi-trash",
    acceptProps: { label: "Löschen", severity: "danger" },
    rejectProps: { label: "Abbrechen", severity: "secondary", outlined: true },
    accept: async () => {
      await api.belegLoeschen(b.id);
      sichtbar.value = false;
      emit("gespeichert");
    },
  });
}
</script>

<template>
  <Dialog
    v-model:visible="sichtbar"
    modal
    :header="beleg ? 'Beleg prüfen und bearbeiten' : 'Beleg hinzufügen'"
    class="w-[min(820px,calc(100vw-2rem))]"
  >
    <form class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="speichern">
      <label class="flex flex-col gap-1 text-sm sm:col-span-2 lg:col-span-1">
        Art
        <SelectButton v-model="form.art" :options="arten" option-label="label" option-value="value" :allow-empty="false" fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm sm:col-span-2">
        Steuerfall
        <Select fluid v-model="form.steuerfall" :options="faelle" option-label="label" option-value="value" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Rechnungsdatum
        <DatePicker fluid v-model="form.rechnungsdatum" date-format="dd.mm.yy" show-icon show-button-bar />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Zahlungsdatum
        <DatePicker fluid v-model="form.zahlungsdatum" date-format="dd.mm.yy" show-icon show-button-bar />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Rechnungsnummer
        <InputText fluid v-model="form.rechnungsnummer" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Partner
        <InputText fluid v-model="form.partner" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        USt-IdNr. Partner
        <InputText fluid v-model="form.partner_ustid" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Beschreibung
        <InputText fluid v-model="form.beschreibung" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Netto (Belegwährung)
        <InputNumber fluid v-model="form.netto" locale="de-DE" :min-fraction-digits="2" :max-fraction-digits="2" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Währung
        <InputText fluid v-model="form.waehrung" maxlength="3" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Steuersatz
        <InputNumber fluid v-model="form.steuersatz" locale="de-DE" suffix=" %" :max-fraction-digits="2" />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Steuer laut Rechnung
        <InputNumber
          v-model="form.steuer"
          fluid
          locale="de-DE"
          :min-fraction-digits="2"
          :max-fraction-digits="2"
          placeholder="leer = berechnen"
        />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Kurs (optional)
        <InputNumber fluid v-model="form.kurs" locale="de-DE" :min-fraction-digits="4" :max-fraction-digits="6" placeholder="Monatskurs" />
      </label>
      <div class="flex flex-col gap-2 sm:col-span-2 lg:col-span-3">
        <Message v-if="beleg?.erechnung" severity="success" size="small" icon="pi pi-verified">
          Aus E-Rechnung gelesen ({{ beleg.erechnung }}). Beträge, Datum und Partner stammen aus den strukturierten
          Rechnungsdaten.
        </Message>
        <Message v-for="(h, i) in beleg?.hinweise ?? []" :key="i" severity="warn" size="small">{{ h }}</Message>
        <Message v-if="fehler" severity="error">{{ fehler }}</Message>
      </div>
      <button type="submit" hidden />
    </form>
    <template #footer>
      <Button v-if="beleg" label="Löschen" icon="pi pi-trash" severity="danger" text @click="loeschen" />
      <Button
        v-if="neuLesbar"
        label="Neu einlesen"
        icon="pi pi-refresh"
        severity="secondary"
        text
        :loading="liestNeu"
        v-tooltip.top="'Liest den Beleg erneut aus der Quelle. Änderungen in diesem Dialog gehen dabei verloren.'"
        @click="neuEinlesen"
      />
      <span class="flex-1" />
      <Button label="Abbrechen" severity="secondary" text @click="sichtbar = false" />
      <Button label="Speichern und als geprüft markieren" icon="pi pi-check" :loading="speichert" @click="speichern" />
    </template>
  </Dialog>
</template>
