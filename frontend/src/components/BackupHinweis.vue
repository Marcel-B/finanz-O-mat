<script setup lang="ts">
// Warnung oben, wenn das Nextcloud-Backup fehlschlägt oder zu alt ist (siehe ustva/backup.py)
import Message from "primevue/message";
import { onMounted, onUnmounted } from "vue";

import { backupBeobachten, backupNichtMehrBeobachten, backupWarnung } from "../backup";

onMounted(backupBeobachten);
onUnmounted(backupNichtMehrBeobachten);
</script>

<template>
  <Message v-if="backupWarnung" severity="warn" class="mb-4">
    {{ backupWarnung }} Details unter „Einstellungen“ → „Backup“ und im Container mit
    <code>journalctl -u finanz-o-mat-backup</code>.
  </Message>
</template>
