<script setup lang="ts">
import Button from "primevue/button";
import ConfirmDialog from "primevue/confirmdialog";
import Message from "primevue/message";
import Tab from "primevue/tab";
import TabList from "primevue/tablist";
import TabPanel from "primevue/tabpanel";
import TabPanels from "primevue/tabpanels";
import Tabs from "primevue/tabs";
import Toast from "primevue/toast";
import { useToast } from "primevue/usetoast";
import { onMounted, ref } from "vue";

import { type AnmeldeArt, angemeldet, api, type Status, type Steuerfall } from "./api";
import AdressbuchAnsicht from "./components/AdressbuchAnsicht.vue";
import AnmeldeAnsicht from "./components/AnmeldeAnsicht.vue";
import AppLogo from "./components/AppLogo.vue";
import BackupHinweis from "./components/BackupHinweis.vue";
import EinstellungenAnsicht from "./components/EinstellungenAnsicht.vue";
import MonatsabschlussAnsicht from "./components/MonatsabschlussAnsicht.vue";
import StundenzettelAnsicht from "./components/StundenzettelAnsicht.vue";
import TaetigkeitenAnsicht from "./components/TaetigkeitenAnsicht.vue";
import UebersichtAnsicht from "./components/UebersichtAnsicht.vue";
import UpdateHinweis from "./components/UpdateHinweis.vue";
import VoranmeldungAnsicht from "./components/VoranmeldungAnsicht.vue";
import { inZwischenablage } from "./zwischenablage";

const status = ref<Status | null>(null);
const steuerfaelle = ref<Steuerfall[]>([]);
const fehler = ref("");
const tab = ref("voranmeldung");
// Sprung aus dem Monatsabschluss in die Voranmeldung eines Monats
const voranmeldungZiel = ref<{ zeitraum: string; n: number } | null>(null);
const voranmeldungStand = ref(0); // zählt hoch, wenn der Monatsabschluss Daten geändert hat

function abschlussGeaendert() {
  voranmeldungStand.value++;
  statusLaden(); // Karte „Nächste Abgabe“ nach abgelegtem Protokoll
}

function oeffnen(reiter: string, zeitraum: string) {
  if (reiter === "voranmeldung") voranmeldungZiel.value = { zeitraum, n: (voranmeldungZiel.value?.n ?? 0) + 1 };
  tab.value = reiter;
}
const toast = useToast();

async function ustidKopieren() {
  const ustid = status.value?.eigene_ustid ?? "";
  if (await inZwischenablage(ustid)) {
    toast.add({ severity: "info", summary: "USt-IdNr. kopiert", detail: ustid, life: 1500 });
  } else {
    toast.add({ severity: "warn", summary: "Kopieren nicht möglich", detail: ustid, life: 4000 });
  }
}

// Ist ein Passwort gesetzt? Sonst gibt es nichts abzumelden.
const mitPasswort = ref(false);
const anmeldeArt = ref<AnmeldeArt>("passwort");

async function statusLaden() {
  try {
    status.value = await api.status();
    mitPasswort.value = status.value.anmeldung;
  } catch (e) {
    fehler.value = (e as Error).message;
  }
}

async function starten() {
  fehler.value = "";
  try {
    const a = await api.anmeldung();
    mitPasswort.value = a.aktiv;
    anmeldeArt.value = a.art;
    angemeldet.value = a.angemeldet;
    if (!a.angemeldet) return;
    [status.value, steuerfaelle.value] = await Promise.all([api.status(), api.steuerfaelle()]);
  } catch (e) {
    if (angemeldet.value !== false) fehler.value = `Start fehlgeschlagen: ${(e as Error).message}`;
  }
}

async function abmelden() {
  try {
    const r = await api.abmelden();
    if (r?.weiter) {
      window.location.href = r.weiter;
      return;
    }
  } finally {
    angemeldet.value = false;
  }
}

onMounted(starten);
</script>

<template>
  <Toast />
  <ConfirmDialog />
  <header class="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 pt-6 pb-4">
    <div class="flex items-center gap-3">
      <AppLogo :groesse="48" />
      <div>
        <h1 class="text-2xl font-semibold">Finanz-<span class="text-primary">O</span>-Mat</h1>
        <p class="text-muted-color">Umsatzsteuer-Voranmeldung berechnen, abgeben in Mein ELSTER</p>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-3">
      <div
        v-if="status && angemeldet"
        class="flex items-center gap-2 rounded-lg border border-surface-200 bg-surface-0 py-1 pr-1 pl-4 dark:border-surface-700 dark:bg-surface-900"
      >
        <div>
          <div class="text-sm text-muted-color">USt-IdNr.</div>
          <div v-if="status.eigene_ustid" class="font-mono font-semibold tracking-wide">{{ status.eigene_ustid }}</div>
          <button v-else type="button" class="text-sm text-primary hover:underline" @click="tab = 'einstellungen'">
            in den Einstellungen eintragen
          </button>
        </div>
        <Button
          v-if="status.eigene_ustid"
          v-tooltip.bottom="'In die Zwischenablage kopieren'"
          icon="pi pi-copy"
          severity="secondary"
          text
          rounded
          aria-label="USt-IdNr. kopieren"
          @click="ustidKopieren"
        />
      </div>
      <div
        v-if="status && angemeldet"
        class="rounded-lg border border-surface-200 bg-surface-0 px-4 py-2 dark:border-surface-700 dark:bg-surface-900"
        :class="status.frist.tage_bis <= 3 ? 'border-l-4 border-l-red-500' : 'border-l-4 border-l-primary'"
      >
        <div class="text-sm text-muted-color">Nächste Abgabe</div>
        <div class="font-semibold">UStVA {{ status.frist.zeitraum_text }}</div>
        <div class="text-sm">
          bis {{ status.frist.faellig_text }} ·
          {{ status.frist.tage_bis === 0 ? "heute" : `noch ${status.frist.tage_bis} Tag${status.frist.tage_bis === 1 ? "" : "e"}` }}
        </div>
        <div
          v-if="status.frist.abgegeben.length"
          v-tooltip.bottom="'ELSTER-Protokoll liegt in Paperless'"
          class="mt-1 text-xs text-muted-color"
        >
          <i class="pi pi-check-circle mr-1 text-xs text-green-500" />{{ status.frist.abgegeben.join(", ") }} abgegeben
        </div>
      </div>
      <Button
        v-if="angemeldet && mitPasswort"
        v-tooltip.bottom="'Abmelden'"
        icon="pi pi-sign-out"
        severity="secondary"
        text
        rounded
        aria-label="Abmelden"
        @click="abmelden"
      />
    </div>
  </header>

  <main v-if="angemeldet === false" class="mx-auto max-w-7xl px-4 pb-8">
    <AnmeldeAnsicht :art="anmeldeArt" @angemeldet="starten" />
  </main>
  <main v-else class="mx-auto max-w-7xl px-4 pb-8">
    <template v-if="angemeldet">
      <UpdateHinweis />
      <BackupHinweis />
    </template>
    <Message v-if="fehler" severity="error" class="mb-4">{{ fehler }}</Message>
    <Tabs v-if="status && angemeldet" v-model:value="tab">
      <TabList>
        <Tab value="voranmeldung"><i class="pi pi-file-check mr-2" />Voranmeldung</Tab>
        <Tab value="monatsabschluss"><i class="pi pi-list-check mr-2" />Monatsabschluss</Tab>
        <Tab value="uebersicht"><i class="pi pi-chart-bar mr-2" />Übersicht</Tab>
        <Tab value="taetigkeiten"><i class="pi pi-briefcase mr-2" />Tätigkeiten</Tab>
        <Tab value="stundenzettel"><i class="pi pi-clock mr-2" />Stundenzettel</Tab>
        <Tab value="adressbuch"><i class="pi pi-address-book mr-2" />Adressbuch</Tab>
        <Tab value="einstellungen"><i class="pi pi-cog mr-2" />Einstellungen</Tab>
      </TabList>
      <TabPanels class="!bg-transparent !px-0">
        <TabPanel value="voranmeldung">
          <VoranmeldungAnsicht
            :status="status"
            :steuerfaelle="steuerfaelle"
            :springe-zu="voranmeldungZiel"
            :neu-laden="voranmeldungStand"
          />
        </TabPanel>
        <TabPanel value="monatsabschluss">
          <MonatsabschlussAnsicht
            :status="status"
            :aktiv="tab === 'monatsabschluss'"
            @oeffnen="oeffnen"
            @geaendert="abschlussGeaendert"
          />
        </TabPanel>
        <TabPanel value="uebersicht">
          <UebersichtAnsicht v-if="tab === 'uebersicht'" :status="status" />
        </TabPanel>
        <TabPanel value="taetigkeiten">
          <TaetigkeitenAnsicht v-if="tab === 'taetigkeiten'" />
        </TabPanel>
        <TabPanel value="stundenzettel">
          <StundenzettelAnsicht v-if="tab === 'stundenzettel'" />
        </TabPanel>
        <TabPanel value="adressbuch">
          <AdressbuchAnsicht v-if="tab === 'adressbuch'" />
        </TabPanel>
        <TabPanel value="einstellungen">
          <EinstellungenAnsicht v-if="tab === 'einstellungen'" @geaendert="statusLaden" />
        </TabPanel>
      </TabPanels>
    </Tabs>
  </main>

  <footer class="mx-auto max-w-7xl px-4 pb-8 text-sm text-muted-color">
    Keine Steuerberatung. Die App übermittelt nichts ans Finanzamt; Abgabe und Zahlung erledigst du selbst in Mein
    ELSTER. Bei Sonderfällen beim Finanzamt oder Steuerberater nachfragen.
  </footer>
</template>
