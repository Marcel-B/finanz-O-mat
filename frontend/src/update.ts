// Gemeinsamer Update-Stand für Hinweis und Versionskarte (siehe ustva/aktualisierung.py)
import { computed, ref } from "vue";

import { api, type UpdateStatus } from "./api";

export const updateStatus = ref<UpdateStatus | null>(null);
export const updateFehler = ref("");

export const updateLaeuft = computed(
  () => updateStatus.value?.zustand === "laeuft" || updateStatus.value?.zustand === "angefordert",
);
export const pruefungLaeuft = computed(() => !!updateStatus.value?.pruefung_laeuft);

let timer: ReturnType<typeof setTimeout> | undefined;
// Installierte Version vor einem Update aus der App; danach Seite neu laden
let vorher: string | null | undefined;

export async function updateLaden() {
  try {
    const neu = await api.update();
    if (vorher !== undefined && neu.installiert && neu.installiert !== vorher && neu.zustand === "aktuell") {
      window.location.reload();
      return;
    }
    updateStatus.value = neu;
    updateFehler.value = "";
  } catch {
    // Während des Neustarts ist die App kurz nicht erreichbar
    if (!updateLaeuft.value) updateFehler.value = "Update-Status nicht abrufbar";
  }
  clearTimeout(timer);
  const schnell = updateLaeuft.value || pruefungLaeuft.value;
  timer = setTimeout(updateLaden, schnell ? 3000 : 15 * 60 * 1000);
}

export function updateStoppen() {
  clearTimeout(timer);
}

export async function updateStarten() {
  vorher = updateStatus.value?.installiert ?? null;
  updateStatus.value = await api.updateStarten();
  clearTimeout(timer);
  timer = setTimeout(updateLaden, 3000);
}

export async function nachUpdatesSuchen() {
  updateStatus.value = await api.updatePruefen();
  clearTimeout(timer);
  timer = setTimeout(updateLaden, 2000);
}
