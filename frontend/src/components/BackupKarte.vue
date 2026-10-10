<script setup lang="ts">
// Stand des Nextcloud-Backups und „Jetzt sichern“ (nur im Proxmox-Container, siehe ustva/backup.py)
import Button from "primevue/button";
import Message from "primevue/message";
import { computed, onMounted, onUnmounted, ref } from "vue";

import {
  backupBeobachten,
  backupLaeuft as laeuft,
  backupNichtMehrBeobachten,
  backupStarten,
  backupStatus as status,
  backupWarnung,
} from "../backup";
import KarteBox from "./KarteBox.vue";

const fehler = ref("");

const zeitpunkt = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }) : "–";

const groesse = computed(() => {
  const b = status.value?.groesse;
  if (b == null) return "–";
  if (b < 1024 * 1024) return `${Math.max(1, Math.round(b / 1024))} KB`;
  return `${(b / 1024 / 1024).toLocaleString("de-DE", { maximumFractionDigits: 1 })} MB`;
});

const zustandText = computed(() => {
  switch (status.value?.zustand) {
    case "ok":
      return "In Ordnung";
    case "fehler":
      return `Fehlgeschlagen${status.value.meldung ? `: ${status.value.meldung}` : ""}`;
    case "laeuft":
    case "angefordert":
      return "Backup läuft";
    default:
      return "–";
  }
});

async function sichern() {
  fehler.value = "";
  try {
    await backupStarten();
  } catch (e) {
    fehler.value = (e as Error).message;
  }
}

onMounted(backupBeobachten);
onUnmounted(backupNichtMehrBeobachten);
</script>

<template>
  <KarteBox v-if="status?.unterstuetzt" titel="Backup">
    <p v-if="status.zustand === 'aus'" class="text-sm">
      Noch nicht eingerichtet. Im Container (<code>pct enter &lt;CTID&gt;</code>) einmal
      <code>finanz-o-mat-backup einrichten</code> ausführen; das fragt nach Nextcloud-Adresse, Benutzer und App-Passwort.
    </p>
    <template v-else>
      <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt class="text-muted-color">Ziel</dt>
        <dd>{{ status.ziel ?? "–" }}</dd>
        <dt class="text-muted-color">Letztes Backup</dt>
        <dd :class="{ 'text-orange-600 dark:text-orange-400': backupWarnung }">
          {{ zeitpunkt(status.letzte_sicherung) }}
        </dd>
        <dt class="text-muted-color">Stand</dt>
        <dd>{{ zustandText }}</dd>
        <dt class="text-muted-color">Gespeichert</dt>
        <dd>{{ status.anzahl ?? "–" }} Stände, je {{ groesse }}</dd>
      </dl>
      <p class="mt-2 text-xs text-muted-color">
        Datenbank und Konfiguration gehen täglich verschlüsselt auf die Nextcloud. Es bleiben 30 tägliche, 12 monatliche
        und 10 jährliche Stände.
      </p>
      <Message v-if="fehler" severity="error" size="small" class="mt-2">{{ fehler }}</Message>
      <div class="mt-3">
        <Button label="Jetzt sichern" icon="pi pi-cloud-upload" severity="secondary" :loading="laeuft" @click="sichern" />
      </div>
    </template>
  </KarteBox>
</template>
