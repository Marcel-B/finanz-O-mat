// Gemeinsamer Backup-Stand für Hinweis und Backup-Karte (siehe ustva/backup.py)
import { computed, ref } from "vue";

import { api, type BackupStatus } from "./api";

export const backupStatus = ref<BackupStatus | null>(null);

export const backupLaeuft = computed(
  () => backupStatus.value?.zustand === "laeuft" || backupStatus.value?.zustand === "angefordert",
);

// Ohne erfolgreiches Backup seit so vielen Tagen warnt die App (der Timer sichert täglich)
const WARNEN_NACH_TAGEN = 3;

export const backupAlterTage = computed(() => {
  const letzte = backupStatus.value?.letzte_sicherung;
  return letzte ? Math.floor((Date.now() - new Date(letzte).getTime()) / 86_400_000) : null;
});

export const backupWarnung = computed(() => {
  const s = backupStatus.value;
  if (!s?.unterstuetzt || s.zustand === "aus" || backupLaeuft.value) return "";
  if (s.zustand === "fehler") return `Das letzte Backup ist fehlgeschlagen${s.meldung ? `: ${s.meldung}` : ""}.`;
  if (backupAlterTage.value === null) return "Es gibt noch kein Backup.";
  if (backupAlterTage.value >= WARNEN_NACH_TAGEN) return `Das letzte Backup ist ${backupAlterTage.value} Tage alt.`;
  return "";
});

let timer: ReturnType<typeof setTimeout> | undefined;
let nutzer = 0;

export async function backupLaden() {
  try {
    backupStatus.value = await api.backup();
  } catch {
    // Kein Backup-Stand ist kein Grund für eine Fehlermeldung; der nächste Abruf versucht es wieder
  }
  clearTimeout(timer);
  timer = setTimeout(backupLaden, backupLaeuft.value ? 3000 : 30 * 60 * 1000);
}

// Hinweis und Karte teilen sich einen Abruf; der läuft, solange eine der beiden sichtbar ist
export function backupBeobachten() {
  if (nutzer++ === 0) void backupLaden();
}

export function backupNichtMehrBeobachten() {
  if (--nutzer === 0) clearTimeout(timer);
}

export async function backupStarten() {
  backupStatus.value = await api.backupStarten();
  clearTimeout(timer);
  timer = setTimeout(backupLaden, 2000);
}
