<script setup lang="ts">
// Installierte Version und „Nach Updates suchen“ (nur im Proxmox-Container, siehe ustva/aktualisierung.py)
import Button from "primevue/button";
import Message from "primevue/message";
import { computed, ref } from "vue";

import { nachUpdatesSuchen, pruefungLaeuft, updateLaeuft, updateStatus as status } from "../update";
import KarteBox from "./KarteBox.vue";

const fehler = ref("");

const zeitpunkt = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }) : "–";

const zustandText = computed(() => {
  switch (status.value?.zustand) {
    case "aktuell":
      return "Aktuell";
    case "neu":
      return "Neue Version verfügbar (Hinweis oben)";
    case "angefordert":
    case "laeuft":
      return "Update läuft";
    case "fehler":
      return "Letztes Update fehlgeschlagen";
    default:
      return status.value?.meldung || "Noch nicht geprüft";
  }
});

async function suchen() {
  fehler.value = "";
  try {
    await nachUpdatesSuchen();
  } catch (e) {
    fehler.value = (e as Error).message;
  }
}
</script>

<template>
  <KarteBox v-if="status?.unterstuetzt" titel="Version">
    <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      <dt class="text-muted-color">Installiert</dt>
      <dd>
        <code>{{ status.installiert ?? "–" }}</code>
        <span v-if="status.installiert_datum" class="text-muted-color"> vom {{ zeitpunkt(status.installiert_datum) }}</span>
      </dd>
      <dt class="text-muted-color">Stand</dt>
      <dd>{{ zustandText }}</dd>
      <dt class="text-muted-color">Zuletzt geprüft</dt>
      <dd>{{ zeitpunkt(status.geprueft) }}</dd>
    </dl>
    <p class="mt-2 text-xs text-muted-color">
      Der Container sieht stündlich nach neuen Versionen{{ status.automatisch ? " und installiert sie automatisch" : "" }}.
    </p>
    <Message v-if="fehler" severity="error" size="small" class="mt-2">{{ fehler }}</Message>
    <div class="mt-3">
      <Button
        label="Nach Updates suchen"
        icon="pi pi-refresh"
        severity="secondary"
        :loading="pruefungLaeuft"
        :disabled="updateLaeuft"
        @click="suchen"
      />
    </div>
  </KarteBox>
</template>
