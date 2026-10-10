<script setup lang="ts">
// Hinweis auf eine neue Version im Proxmox-Container, mit „Jetzt aktualisieren“ (siehe ustva/aktualisierung.py)
import Button from "primevue/button";
import Message from "primevue/message";
import { useConfirm } from "primevue/useconfirm";
import { computed, onMounted, onUnmounted, ref } from "vue";

import { api, type UpdateStatus } from "../api";

const confirm = useConfirm();
const status = ref<UpdateStatus | null>(null);
const fehler = ref("");
let timer: ReturnType<typeof setTimeout> | undefined;
let vorher: string | null | undefined;

const laeuft = computed(() => status.value?.zustand === "laeuft" || status.value?.zustand === "angefordert");

async function laden() {
  try {
    const neu = await api.update();
    // Nach dem Update läuft eine neue Version: Seite neu laden, damit auch die Oberfläche neu ist
    if (vorher !== undefined && neu.installiert && neu.installiert !== vorher && neu.zustand === "aktuell") {
      window.location.reload();
      return;
    }
    status.value = neu;
    fehler.value = "";
  } catch {
    // Während des Neustarts ist die App kurz nicht erreichbar
    if (!laeuft.value) fehler.value = "Update-Status nicht abrufbar";
  }
  clearTimeout(timer);
  timer = setTimeout(laden, laeuft.value ? 3000 : 15 * 60 * 1000);
}

function aktualisieren() {
  confirm.require({
    header: "Finanz-O-Mat aktualisieren",
    message: "Die Datenbank wird vorher gesichert. Die App ist während des Updates ein bis zwei Minuten nicht erreichbar.",
    icon: "pi pi-download",
    acceptProps: { label: "Jetzt aktualisieren" },
    rejectProps: { label: "Abbrechen", severity: "secondary", outlined: true },
    accept: async () => {
      try {
        vorher = status.value?.installiert ?? null;
        status.value = await api.updateStarten();
        clearTimeout(timer);
        timer = setTimeout(laden, 3000);
      } catch (e) {
        fehler.value = (e as Error).message;
      }
    },
  });
}

onMounted(laden);
onUnmounted(() => clearTimeout(timer));
</script>

<template>
  <template v-if="status?.unterstuetzt">
    <Message v-if="status.zustand === 'neu'" severity="info" class="mb-4" :pt="{ text: { class: 'flex-1' } }">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div class="font-semibold">Neue Version verfügbar</div>
          <ul v-if="status.aenderungen.length" class="mt-1 list-disc pl-5 text-sm">
            <li v-for="a in status.aenderungen.slice(0, 5)" :key="a">{{ a }}</li>
            <li v-if="status.aenderungen.length > 5">und {{ status.aenderungen.length - 5 }} weitere</li>
          </ul>
        </div>
        <Button label="Jetzt aktualisieren" icon="pi pi-download" size="small" @click="aktualisieren" />
      </div>
    </Message>
    <Message v-else-if="laeuft" severity="secondary" class="mb-4">
      <i class="pi pi-spin pi-spinner mr-2" />Update läuft, die Seite lädt danach neu.
    </Message>
    <Message v-else-if="status.zustand === 'fehler'" severity="warn" class="mb-4">
      Das letzte Update ist fehlgeschlagen{{ status.meldung ? `: ${status.meldung}` : "" }}. Die bisherige Version
      läuft weiter; Details im Container mit <code>journalctl -u finanz-o-mat-update</code>.
    </Message>
  </template>
  <Message v-if="fehler" severity="warn" class="mb-4">{{ fehler }}</Message>
</template>
