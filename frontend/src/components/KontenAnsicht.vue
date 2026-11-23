<script setup lang="ts">
// Reiter „Konten“: Salden und Umsätze aller über Enable Banking verbundenen Konten, nur zur Übersicht.
// Mit der Buchhaltung (Kontoumsätze, Belege) hat die Seite nichts zu tun. Dazu Depots über FinTS (nur Bestand).
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import DatePicker from "primevue/datepicker";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import SelectButton from "primevue/selectbutton";
import Tag from "primevue/tag";
import { useToast } from "primevue/usetoast";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import { api, type BankKonto, type KontenUebersicht, type KontoBuchung, type KontoVerlauf } from "@/api";
import { alsIso, betrag, datum, passtZurSuche, suchtext } from "@/format";
import { inZwischenablage } from "@/zwischenablage";

import DepotAnsicht from "./DepotAnsicht.vue";
import SuchFeld from "./SuchFeld.vue";
import VerlaufChart from "./VerlaufChart.vue";

const toast = useToast();
const daten = ref<KontenUebersicht | null>(null);
const buchungen = ref<KontoBuchung[]>([]);
const auswahl = ref<string>(""); // "" = alle Konten, sonst Schlüssel eines Kontos oder Depots
const suche = ref("");
const laedt = ref(false);
const ruftAb = ref(false);
const fehler = ref("");

const konten = computed(() => daten.value?.konten ?? []);
const gewaehlt = computed(() => konten.value.find((k) => k.schluessel === auswahl.value) ?? null);
const depots = computed(() => daten.value?.depots ?? []);
const gewaehltesDepot = computed(() => depots.value.find((d) => d.schluessel === auswahl.value) ?? null);
const mitDepot = computed(() => depots.value.length > 0 || !!daten.value?.depot.eingerichtet);
const nachSchluessel = computed(() => Object.fromEntries(konten.value.map((k) => [k.schluessel, k])));

async function laden(anzeigen = true) {
  if (anzeigen) laedt.value = true;
  fehler.value = "";
  try {
    daten.value = await api.konten();
    // Ohne Konten gleich das erste Depot zeigen
    if (!auswahl.value && !daten.value.konten.length && daten.value.depots.length) {
      auswahl.value = daten.value.depots[0].schluessel;
    }
    if (!gewaehltesDepot.value) {
      buchungen.value = await api.kontoBuchungen(auswahl.value || undefined);
      void verlaufLaden();
    }
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    laedt.value = false;
  }
}

watch(auswahl, async () => {
  suche.value = "";
  if (gewaehltesDepot.value) return;
  verlauf.value = null;
  try {
    buchungen.value = await api.kontoBuchungen(auswahl.value || undefined);
    void verlaufLaden();
  } catch (e) {
    fehler.value = (e as Error).message;
  }
});

// Verlauf des Kontostands (bzw. der Summe aller Euro-Konten), ein- und ausblendbar
const verlauf = ref<KontoVerlauf | null>(null);
const VERLAUF_SPEICHER = "konten.verlauf";
const verlaufZeigen = ref(lesen(VERLAUF_SPEICHER) !== "aus");
watch(verlaufZeigen, (an) => schreiben(VERLAUF_SPEICHER, an ? "an" : "aus"));

function lesen(schluessel: string): string | null {
  try {
    return localStorage.getItem(schluessel);
  } catch {
    return null;
  }
}

function schreiben(schluessel: string, wert: string) {
  try {
    localStorage.setItem(schluessel, wert);
  } catch {
    /* ohne Speicher eben nur bis zum Neuladen */
  }
}

async function verlaufLaden() {
  const konto = auswahl.value;
  try {
    const v = await api.kontoVerlauf(konto || undefined);
    if (auswahl.value === konto) verlauf.value = v;
  } catch {
    if (auswahl.value === konto) verlauf.value = null;
  }
}

// Zeitraum links über den Umsätzen; gilt für Tabelle und Verlauf
const zeitraum = ref<(Date | null)[] | null>(null);
const vonIso = computed(() => alsIso(zeitraum.value?.[0]) ?? "");
const bisIso = computed(() => alsIso(zeitraum.value?.[1]) ?? "");

function laufend(art: "monat" | "quartal"): [Date, Date] {
  const heute = new Date();
  const erster = art === "monat" ? heute.getMonth() : heute.getMonth() - (heute.getMonth() % 3);
  const monate = art === "monat" ? 1 : 3;
  return [new Date(heute.getFullYear(), erster, 1), new Date(heute.getFullYear(), erster + monate, 0)];
}

const schnellOptionen = [
  { label: "Laufender Monat", value: "monat" },
  { label: "Laufendes Quartal", value: "quartal" },
] as const;
// Die Schnellauswahl ist gewählt, solange der Zeitraum genau passt; ein anderer Zeitraum hebt sie auf
const schnell = computed({
  get: () =>
    schnellOptionen.find((o) => {
      const [von, bis] = laufend(o.value);
      return vonIso.value === alsIso(von) && bisIso.value === alsIso(bis);
    })?.value ?? null,
  set: (art: "monat" | "quartal" | null) => (zeitraum.value = art ? laufend(art) : null),
});

const imZeitraum = (tag: string) => (!vonIso.value || tag >= vonIso.value) && (!bisIso.value || tag <= bisIso.value);
const verlaufPunkte = computed(() => (verlauf.value?.punkte ?? []).filter((p) => imZeitraum(p.tag)));

// „Jetzt abrufen“: Enable Banking, danach die ING-Konten über FinTS. In dieser Reihenfolge, damit FinTS erkennt,
// was Enable Banking eben geliefert hat. Der Server schickt die Angaben des Browsers mit, dann gilt das Limit von 4
// Abrufen in 24 Stunden nicht; nur wenn eine Bank das ablehnt, zählt der Abruf.
async function abrufen() {
  ruftAb.value = true;
  const zeilen: string[] = [];
  let neu = 0;
  let warnen = false;
  try {
    if (daten.value?.eingerichtet) {
      try {
        const e = await api.kontenAbrufen();
        // Ein ING-Konto ohne freie Abrufe holt gleich FinTS, das ist kein Problem
        const probleme = e.konten.filter(
          (k) => k.fehler || (k.uebersprungen && !(mitIng.value && nachSchluessel.value[k.schluessel]?.ing)),
        );
        neu += e.konten.reduce((n, k) => n + k.neu, 0);
        warnen ||= probleme.some((k) => k.fehler);
        zeilen.push(...probleme.map((k) => `${k.name}: ${k.fehler || k.uebersprungen}`));
      } catch (e) {
        warnen = true;
        zeilen.push((e as Error).message);
      }
    }
    if (mitIng.value) {
      toast.add({ severity: "info", summary: "ING wird abgerufen", detail: "Falls die ING fragt: in der ING-App freigeben.", life: 5000 });
      try {
        const e = await api.ingAbrufen();
        neu += e.neu;
        if (e.fehler) {
          warnen = true;
          zeilen.push(`ING: ${e.freigabe ? "keine Freigabe in der ING-App" : e.fehler}`);
        }
      } catch (e) {
        warnen = true;
        zeilen.push(`ING: ${(e as Error).message}`);
      }
    }
    toast.add({
      severity: warnen ? "warn" : "success",
      summary: neu ? `${neu} neue Umsätze` : "Keine neuen Umsätze",
      detail: zeilen.join(" · ") || undefined,
      life: zeilen.length ? 8000 : 3000,
    });
    await laden();
  } finally {
    ruftAb.value = false;
  }
}

// Beim Öffnen und wenn die App wieder in den Vordergrund kommt (nach einer Push-Nachricht der Bank): Enable Banking
// nur mit den Angaben des Browsers, also ohne das Tageslimit anzutasten, danach die ING-Konten über FinTS. Der Server
// ruft je Konto höchstens alle zwei Minuten ab und fragt keine Freigabe nach, auf die die ING wartet.
const mitIng = computed(() => !!daten.value?.ing?.eingerichtet);
const oeffnetGerade = ref(false);

async function beimOeffnen() {
  if (oeffnetGerade.value || ruftAb.value || !daten.value) return;
  if (!daten.value.eingerichtet && !mitIng.value) return;
  oeffnetGerade.value = true;
  let neu = false;
  try {
    if (daten.value.eingerichtet) {
      try {
        neu = (await api.kontenAbrufen(true)).konten.some((k) => !k.uebersprungen);
      } catch {
        // Beim Öffnen still; der Fehler steht danach am Konto bzw. im Log
      }
    }
    if (mitIng.value) {
      try {
        const e = await api.ingAbrufen(true);
        neu ||= !e.uebersprungen;
        if (e.freigabe) {
          toast.add({ severity: "warn", summary: "Keine Freigabe in der ING-App", detail: e.fehler, life: 8000 });
        }
      } catch {
        // wie oben
      }
    }
    if (neu) await laden(false);
  } finally {
    oeffnetGerade.value = false;
  }
}

function sichtbar() {
  if (document.visibilityState === "visible") void beimOeffnen();
}

// Depotbestand über FinTS; verlangt die ING eine Freigabe in der App, wartet der Server bis zu zwei Minuten
const ruftDepotAb = ref(false);

async function depotAbrufen() {
  ruftDepotAb.value = true;
  toast.add({
    severity: "info",
    summary: "Depot wird abgerufen",
    detail: "Falls die ING fragt: in der ING-App freigeben.",
    life: 8000,
  });
  try {
    const e = await api.depotAbrufen();
    if (e.fehler) {
      toast.add({
        severity: "warn",
        summary: e.freigabe ? "Keine Freigabe in der ING-App" : "Depotabruf fehlgeschlagen",
        detail: e.fehler,
        life: 10000,
      });
    } else {
      const n = e.depots.reduce((s, d) => s + d.positionen, 0);
      toast.add({ severity: "success", summary: "Depot abgerufen", detail: `${n} Positionen`, life: 3000 });
    }
    await laden();
  } catch (e) {
    toast.add({ severity: "error", summary: "Depotabruf fehlgeschlagen", detail: (e as Error).message, life: 8000 });
  } finally {
    ruftDepotAb.value = false;
  }
}

// Eigener Name (leer = wieder der Name der Bank) und Farbe für ein Konto oder Depot
const umbenennen = ref<{ schluessel: string; name: string; bank: string; farbe: string; depot: boolean } | null>(null);
const speichertName = ref(false);
// Gut unterscheidbar, in Hell und Dunkel lesbar
const FARBEN = [
  { wert: "#3b82f6", name: "Blau" },
  { wert: "#06b6d4", name: "Türkis" },
  { wert: "#14b8a6", name: "Petrol" },
  { wert: "#22c55e", name: "Grün" },
  { wert: "#84cc16", name: "Hellgrün" },
  { wert: "#eab308", name: "Gelb" },
  { wert: "#f97316", name: "Orange" },
  { wert: "#ef4444", name: "Rot" },
  { wert: "#ec4899", name: "Pink" },
  { wert: "#8b5cf6", name: "Violett" },
  { wert: "#a16207", name: "Braun" },
  { wert: "#64748b", name: "Grau" },
];
const eigeneFarbe = computed(() => !!umbenennen.value?.farbe && !FARBEN.some((f) => f.wert === umbenennen.value?.farbe));

function umbenennenOeffnen(schluessel: string) {
  const k = konten.value.find((k) => k.schluessel === schluessel);
  const d = depots.value.find((d) => d.schluessel === schluessel);
  if (k) umbenennen.value = { schluessel, name: k.eigener_name, bank: k.bezeichnung || k.bank, farbe: k.farbe, depot: false };
  else if (d) umbenennen.value = { schluessel, name: d.eigener_name, bank: d.bezeichnung, farbe: d.farbe, depot: true };
}

async function nameSpeichern() {
  const u = umbenennen.value;
  if (!u) return;
  speichertName.value = true;
  try {
    await api.kontoAnzeige(u.schluessel, { name: u.name, farbe: u.farbe });
    umbenennen.value = null;
    await laden(false);
  } catch (e) {
    toast.add({ severity: "error", summary: "Speichern fehlgeschlagen", detail: (e as Error).message, life: 6000 });
  } finally {
    speichertName.value = false;
  }
}

// Reihenfolge der Kontenliste: im Sortiermodus ziehen oder mit den Pfeilen verschieben, gespeichert nach jedem Schritt
type Gruppe = "konten" | "depots";
const sortieren = ref(false);
const gezogen = ref<{ gruppe: Gruppe; schluessel: string } | null>(null);
const liste = (gruppe: Gruppe): { schluessel: string }[] =>
  (gruppe === "konten" ? daten.value?.konten : daten.value?.depots) ?? [];

function verschieben(gruppe: Gruppe, schluessel: string, nach: number) {
  const l = liste(gruppe);
  const von = l.findIndex((e) => e.schluessel === schluessel);
  if (von < 0 || nach < 0 || nach >= l.length || von === nach) return false;
  l.splice(nach, 0, ...l.splice(von, 1));
  return true;
}

async function reihenfolgeSpeichern(gruppe: Gruppe) {
  try {
    await api.kontoReihenfolge(liste(gruppe).map((e) => e.schluessel));
  } catch (e) {
    toast.add({ severity: "error", summary: "Reihenfolge nicht gespeichert", detail: (e as Error).message, life: 6000 });
    await laden(false);
  }
}

function pfeil(gruppe: Gruppe, schluessel: string, schritt: number) {
  const i = liste(gruppe).findIndex((e) => e.schluessel === schluessel);
  if (verschieben(gruppe, schluessel, i + schritt)) void reihenfolgeSpeichern(gruppe);
}

function ziehen(gruppe: Gruppe, schluessel: string, e: DragEvent) {
  gezogen.value = { gruppe, schluessel };
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", schluessel);
  }
}

// Beim Darüberziehen gleich umsortieren, so sieht man, wo das Konto landet
function darueber(gruppe: Gruppe, schluessel: string) {
  const g = gezogen.value;
  if (!g || g.gruppe !== gruppe || g.schluessel === schluessel) return;
  verschieben(gruppe, g.schluessel, liste(gruppe).findIndex((e) => e.schluessel === schluessel));
}

function abgelegt() {
  const g = gezogen.value;
  gezogen.value = null;
  if (g) void reihenfolgeSpeichern(g.gruppe);
}

// Alte Kontoauszüge (CAMT.053, z. B. aus MoneyMoney) in die Übersicht
const dateiFeld = ref<HTMLInputElement | null>(null);
const liestEin = ref(false);

async function einlesen(ereignis: Event) {
  const feld = ereignis.target as HTMLInputElement;
  const dateien = [...(feld.files ?? [])];
  feld.value = "";
  if (!dateien.length) return;
  liestEin.value = true;
  try {
    const zeilen: string[] = [];
    let neu = 0;
    let fehler = false;
    for (const datei of dateien) {
      try {
        for (const a of (await api.kontoauszugEinlesen(datei)).auszuege) {
          if (a.fehler) {
            fehler = true;
            zeilen.push(`${ibanKurz(a.iban)}: ${a.fehler}`);
          } else {
            neu += a.neu;
            const zeitraum = a.von && a.bis ? ` (${datum(a.von)} bis ${datum(a.bis)})` : "";
            zeilen.push(`${a.konto}${zeitraum}: ${a.neu} neu, ${a.vorhanden} schon vorhanden`);
          }
        }
      } catch (e) {
        fehler = true;
        zeilen.push(`${datei.name}: ${(e as Error).message}`);
      }
    }
    toast.add({
      severity: fehler ? "warn" : "success",
      summary: neu ? `${neu} Umsätze eingelesen` : "Keine neuen Umsätze",
      detail: zeilen.join(" · ") || undefined,
      life: fehler ? 10000 : 6000,
    });
    await laden();
  } finally {
    liestEin.value = false;
  }
}

onMounted(async () => {
  await laden();
  void beimOeffnen();
  document.addEventListener("visibilitychange", sichtbar);
});
onUnmounted(() => document.removeEventListener("visibilitychange", sichtbar));

// Uhrzeit in der Zeitzone des Browsers
function zeit(iso: string | null | undefined): string {
  if (!iso) return "nie";
  if (iso.length <= 10) return datum(iso);
  const d = new Date(iso);
  return `${d.toLocaleDateString("de-DE")}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
}

const knoten = (gewaehlt: boolean) => [
  "flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left",
  gewaehlt ? "bg-highlight" : "hover:bg-emphasis",
];

// Farbpunkt; ohne eigene Farbe ein Ring in der Textfarbe
const punkt = (farbe: string) =>
  farbe ? { background: farbe } : { boxShadow: "inset 0 0 0 1.5px var(--p-text-muted-color)" };

const ibanKurz = (iban: string) => (iban ? `${iban.slice(0, 4)} … ${iban.slice(-4)}` : "");
const ibanLang = (iban: string) => iban.replace(/(.{4})/g, "$1 ").trim();

// IBAN und BIC ohne Leerzeichen, so nehmen Überweisungsformulare sie an
async function kopieren(was: string, text: string) {
  if (await inZwischenablage(text)) {
    toast.add({ severity: "info", summary: `${was} kopiert`, detail: text, life: 1500 });
  } else {
    toast.add({ severity: "warn", summary: "Kopieren nicht möglich", detail: text, life: 4000 });
  }
}

const summe = computed(() => Object.entries(daten.value?.summen ?? {}));
const waehrung = (w: string) => (w === "EUR" ? "€" : w);
// Kontostand im Kopf: das gewählte Konto oder die Summe je Währung, dazu Vorgemerktes und der Stand danach
const staende = computed(() => {
  if (gewaehlt.value) {
    const k = gewaehlt.value;
    return [{ waehrung: k.waehrung, saldo: k.saldo, vorgemerkt: k.vorgemerkt, anzahl: k.vorgemerkt_anzahl }];
  }
  const vorgemerkt = daten.value?.summen_vorgemerkt ?? {};
  const anzahl = konten.value.reduce((n, k) => n + k.vorgemerkt_anzahl, 0);
  return summe.value.map(([w, s]) => ({ waehrung: w, saldo: s, vorgemerkt: vorgemerkt[w] ?? null, anzahl }));
});
const danach = (saldo: string | null, vorgemerkt: string | null) => Number(saldo ?? 0) + Number(vorgemerkt ?? 0);
const depotSumme = computed(() => depots.value.reduce((s, d) => s + Number(d.wert ?? 0), 0));
// Enable Banking und FinTS schreiben UTC-Zeitpunkte, die sich als Text sortieren lassen
const zuletzt = (k: BankKonto) => [k.abgerufen, k.fints].filter(Boolean).sort().at(-1) ?? "";
const letzterAbruf = computed(() => konten.value.map(zuletzt).filter(Boolean).sort().at(-1) ?? "");
// „9:00, 12:30, 16:00 und 19:30“
const abrufzeiten = computed(() => {
  const z = (daten.value?.zeiten ?? []).map((t) => t.replace(/^0(\d)/, "$1"));
  return z.length > 1 ? `${z.slice(0, -1).join(", ")} und ${z.at(-1)}` : (z[0] ?? "");
});

function warnung(k: BankKonto): string {
  if (k.fehler) return k.fehler;
  if (k.freigabe_tage !== null && k.freigabe_tage < 0) return "Freigabe abgelaufen";
  if (k.freigabe_tage !== null && k.freigabe_tage <= 14) return `Freigabe läuft in ${k.freigabe_tage} Tagen ab`;
  return "";
}

const imZeitraumGefiltert = computed(() =>
  vonIso.value || bisIso.value ? buchungen.value.filter((b) => imZeitraum(b.buchungsdatum)) : buchungen.value,
);
const gefiltert = computed(() =>
  suche.value.trim()
    ? imZeitraumGefiltert.value.filter((b) =>
        passtZurSuche(
          suchtext(b.buchungsdatum, b.partner, b.verwendungszweck, b.iban, b.betrag, b.typ),
          suche.value.trim(),
        ),
      )
    : imZeitraumGefiltert.value,
);
</script>

<template>
  <Message v-if="fehler" severity="error" class="mb-4">{{ fehler }}</Message>

  <Message v-if="daten && !daten.eingerichtet && !konten.length" severity="info" class="mb-4">
    Noch kein Kontoabruf eingerichtet. Wie das geht (Enable Banking, <code>ENABLE_BANKING_APP_ID</code> und
    <code>ENABLE_BANKING_SCHLUESSEL</code>), steht im README unter „Automatisch abrufen“.
  </Message>
  <Message v-else-if="daten && !konten.length && !mitDepot" severity="info" class="mb-4">
    Noch kein Konto verbunden. Im Container: <code>finanz-o-mat bank verbinden --bank ING</code>
  </Message>

  <!--
    Ab lg ist die Seite genau ein Fenster hoch: Beim Scrollen verschwindet der Kopf der App, danach steht die Seite,
    und Kontenliste und Umsätze scrollen in sich. Darunter bleibt nur der Rand von <main> (pb-8).
  -->
  <div
    v-if="konten.length || mitDepot"
    class="grid gap-4 lg:mt-4 lg:h-[calc(100dvh-4rem)] lg:grid-cols-[18rem_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]"
  >
    <!-- Kontenliste, je Konto ein Knoten -->
    <nav
      class="h-fit rounded-xl border border-surface-200 bg-surface-0 p-2 lg:max-h-full lg:overflow-y-auto dark:border-surface-700 dark:bg-surface-900"
      aria-label="Konten"
    >
      <template v-if="konten.length">
        <div class="flex items-center justify-between gap-2 px-2 pt-1 pb-2 text-sm font-semibold text-muted-color">
          <span>Konten</span>
          <Button
            v-if="konten.length > 1 || depots.length > 1"
            v-tooltip.top="sortieren ? 'Reihenfolge fertig' : 'Reihenfolge ändern'"
            :icon="sortieren ? 'pi pi-check' : 'pi pi-sort-alt'"
            :label="sortieren ? 'Fertig' : undefined"
            size="small"
            :severity="sortieren ? undefined : 'secondary'"
            text
            :aria-label="sortieren ? 'Reihenfolge fertig' : 'Reihenfolge ändern'"
            class="!py-0.5"
            @click="sortieren = !sortieren"
          />
        </div>
        <p v-if="sortieren" class="px-2 pb-2 text-xs text-muted-color">Ziehen oder mit den Pfeilen verschieben.</p>
        <button v-if="!sortieren" type="button" :class="knoten(auswahl === '')" @click="auswahl = ''">
          <i class="pi pi-building-columns text-primary" />
          <span class="min-w-0 flex-1 truncate font-medium">Alle Konten</span>
          <span class="text-right font-semibold tabular-nums">
            <span v-for="[w, s] in summe" :key="w" class="block">{{ betrag(s) }} {{ w === "EUR" ? "€" : w }}</span>
          </span>
        </button>
      </template>
      <template v-if="sortieren">
        <div
          v-for="(k, i) in konten"
          :key="k.schluessel"
          draggable="true"
          class="flex cursor-grab items-center gap-2 rounded-lg px-2 py-1.5 select-none hover:bg-emphasis"
          :class="{ 'opacity-50': gezogen?.schluessel === k.schluessel }"
          @dragstart="ziehen('konten', k.schluessel, $event)"
          @dragover.prevent="darueber('konten', k.schluessel)"
          @drop.prevent
          @dragend="abgelegt"
        >
          <i class="pi pi-bars text-muted-color" />
          <span class="h-2.5 w-2.5 shrink-0 rounded-full" :style="punkt(k.farbe)" />
          <span class="min-w-0 flex-1 truncate">{{ k.name }}</span>
          <Button icon="pi pi-angle-up" size="small" severity="secondary" text rounded :disabled="i === 0" aria-label="Nach oben" class="!h-7 !w-7" @click="pfeil('konten', k.schluessel, -1)" />
          <Button icon="pi pi-angle-down" size="small" severity="secondary" text rounded :disabled="i === konten.length - 1" aria-label="Nach unten" class="!h-7 !w-7" @click="pfeil('konten', k.schluessel, 1)" />
        </div>
      </template>
      <button
        v-for="k in sortieren ? [] : konten"
        :key="k.schluessel"
        type="button"
        :class="[knoten(auswahl === k.schluessel), 'pl-5']"
        @click="auswahl = k.schluessel"
      >
        <i class="pi pi-building-columns" :class="{ 'text-muted-color': !k.farbe }" :style="k.farbe ? { color: k.farbe } : undefined" />
        <span class="min-w-0 flex-1">
          <span class="flex items-center gap-1 truncate">
            {{ k.name }}
            <i
              v-if="warnung(k)"
              v-tooltip.top="warnung(k)"
              class="pi pi-exclamation-triangle text-xs text-orange-500"
            />
          </span>
          <small class="block truncate text-muted-color">{{ k.bank }} · {{ ibanKurz(k.iban) }}</small>
        </span>
        <span class="text-right tabular-nums" :class="{ 'text-red-600 dark:text-red-400': Number(k.saldo) < 0 }">
          {{ k.saldo === null ? "–" : `${betrag(k.saldo)} ${k.waehrung === "EUR" ? "€" : k.waehrung}` }}
        </span>
      </button>

      <!-- Depots über FinTS, nur Bestand -->
      <template v-if="mitDepot">
        <div
          class="flex items-center justify-between gap-2 px-2 pt-1 pb-2 text-sm font-semibold text-muted-color"
          :class="{ 'mt-3': konten.length }"
        >
          <span>Depots</span>
          <Button
            v-if="!konten.length && depots.length > 1"
            v-tooltip.top="sortieren ? 'Reihenfolge fertig' : 'Reihenfolge ändern'"
            :icon="sortieren ? 'pi pi-check' : 'pi pi-sort-alt'"
            size="small"
            severity="secondary"
            text
            :aria-label="sortieren ? 'Reihenfolge fertig' : 'Reihenfolge ändern'"
            class="!py-0.5"
            @click="sortieren = !sortieren"
          />
          <span v-if="depots.length > 1" class="tabular-nums">{{ betrag(depotSumme) }} €</span>
          <Button
            v-if="!depots.length"
            v-tooltip.top="'Fragt die ING nach einer Freigabe, kommt sie in die ING-App'"
            icon="pi pi-refresh"
            label="Abrufen"
            size="small"
            severity="secondary"
            text
            :loading="ruftDepotAb"
            @click="depotAbrufen"
          />
        </div>
        <small v-if="!depots.length && daten?.depot.fehler" class="block px-2 pb-2 text-orange-600 dark:text-orange-400">
          {{ daten.depot.fehler }}
        </small>
        <template v-if="sortieren">
          <div
            v-for="(d, i) in depots"
            :key="d.schluessel"
            draggable="true"
            class="flex cursor-grab items-center gap-2 rounded-lg px-2 py-1.5 select-none hover:bg-emphasis"
            :class="{ 'opacity-50': gezogen?.schluessel === d.schluessel }"
            @dragstart="ziehen('depots', d.schluessel, $event)"
            @dragover.prevent="darueber('depots', d.schluessel)"
            @drop.prevent
            @dragend="abgelegt"
          >
            <i class="pi pi-bars text-muted-color" />
            <span class="h-2.5 w-2.5 shrink-0 rounded-full" :style="punkt(d.farbe)" />
            <span class="min-w-0 flex-1 truncate">{{ d.name }}</span>
            <Button icon="pi pi-angle-up" size="small" severity="secondary" text rounded :disabled="i === 0" aria-label="Nach oben" class="!h-7 !w-7" @click="pfeil('depots', d.schluessel, -1)" />
            <Button icon="pi pi-angle-down" size="small" severity="secondary" text rounded :disabled="i === depots.length - 1" aria-label="Nach unten" class="!h-7 !w-7" @click="pfeil('depots', d.schluessel, 1)" />
          </div>
        </template>
        <button
          v-for="d in sortieren ? [] : depots"
          :key="d.schluessel"
          type="button"
          :class="[knoten(auswahl === d.schluessel), 'pl-5']"
          @click="auswahl = d.schluessel"
        >
          <i class="pi pi-chart-line" :class="{ 'text-muted-color': !d.farbe }" :style="d.farbe ? { color: d.farbe } : undefined" />
          <span class="min-w-0 flex-1">
            <span class="flex items-center gap-1 truncate">
              {{ d.name }}
              <i
                v-if="daten?.depot.freigabe || daten?.depot.fehler"
                v-tooltip.top="daten?.depot.freigabe ? 'Wartet auf Freigabe in der ING-App' : daten?.depot.fehler"
                class="pi pi-exclamation-triangle text-xs text-orange-500"
              />
            </span>
            <small class="block truncate text-muted-color">{{ d.bank }} · {{ d.positionen }} Positionen</small>
          </span>
          <span class="text-right tabular-nums">{{ betrag(d.wert) }} {{ d.waehrung === "EUR" ? "€" : d.waehrung }}</span>
        </button>
      </template>
    </nav>

    <DepotAnsicht
      v-if="gewaehltesDepot && daten"
      :depot="gewaehltesDepot"
      :zugang="daten.depot"
      :ruft-ab="ruftDepotAb"
      :abrufzeiten="abrufzeiten"
      :zeit="zeit"
      v-model:verlauf="verlaufZeigen"
      @abrufen="depotAbrufen"
      @umbenennen="umbenennenOeffnen(gewaehltesDepot.schluessel)"
    />

    <!-- Umsätze des gewählten Kontos -->
    <section
      v-else
      class="min-w-0 rounded-xl border border-surface-200 bg-surface-0 p-4 lg:flex lg:min-h-0 lg:flex-col dark:border-surface-700 dark:bg-surface-900"
    >
      <div class="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="text-lg font-semibold">
            <span v-if="gewaehlt?.farbe" class="mr-1.5 inline-block h-3 w-3 rounded-full align-middle" :style="punkt(gewaehlt.farbe)" />
            {{ gewaehlt ? gewaehlt.name : "Alle Konten" }}
            <Button
              v-if="gewaehlt"
              v-tooltip.top="'Name und Farbe'"
              icon="pi pi-pencil"
              size="small"
              severity="secondary"
              text
              rounded
              aria-label="Name und Farbe"
              class="align-middle"
              @click="umbenennenOeffnen(gewaehlt.schluessel)"
            />
            <Tag
              v-if="gewaehlt?.buchhaltung"
              v-tooltip.top="'Die Umsätze dieses Kontos gehen auch in die Buchhaltung (Kontoumsätze)'"
              value="Buchhaltung"
              severity="secondary"
              class="ml-1 align-middle"
            />
          </h2>
          <div v-if="gewaehlt" class="text-sm text-muted-color">
            {{ gewaehlt.bank }}<template v-if="gewaehlt.eigener_name && gewaehlt.bezeichnung"> · {{ gewaehlt.bezeichnung }}</template
            ><template v-if="gewaehlt.iban">
              · <span class="font-mono">{{ ibanLang(gewaehlt.iban) }}</span
              ><Button
                v-tooltip.bottom="'IBAN in die Zwischenablage kopieren'"
                icon="pi pi-copy"
                severity="secondary"
                text
                rounded
                size="small"
                class="kopieren align-middle"
                aria-label="IBAN kopieren"
                @click="kopieren('IBAN', gewaehlt.iban)"
              /></template
            ><template v-if="gewaehlt.bic">
              · BIC <span class="font-mono">{{ gewaehlt.bic }}</span
              ><Button
                v-tooltip.bottom="'BIC in die Zwischenablage kopieren'"
                icon="pi pi-copy"
                severity="secondary"
                text
                rounded
                size="small"
                class="kopieren align-middle"
                aria-label="BIC kopieren"
                @click="kopieren('BIC', gewaehlt.bic)"
              /></template>
          </div>
          <div class="text-sm text-muted-color">
            <template v-if="gewaehlt">
              Saldo vom {{ zeit(gewaehlt.saldo_stand) }}
              <template v-if="!gewaehlt.nur_fints"> · Freigabe bis {{ zeit(gewaehlt.freigabe_bis.slice(0, 10)) }}</template>
              <template v-if="gewaehlt.fints"> · über FinTS {{ zeit(gewaehlt.fints) }}</template>
            </template>
            <template v-else>Zuletzt abgerufen: {{ zeit(letzterAbruf) }}</template>
            <template v-if="abrufzeiten"> · automatisch um {{ abrufzeiten }} Uhr</template>
            <template v-if="mitIng && (!gewaehlt || gewaehlt.ing)"> · ING stündlich 7–22 Uhr</template>
            <i v-if="oeffnetGerade" v-tooltip.bottom="'Konten werden abgerufen'" class="pi pi-spin pi-spinner ml-1 text-xs" />
          </div>
        </div>
        <div class="flex items-center gap-3">
          <div v-for="s in staende" :key="s.waehrung" class="text-right">
            <div class="text-2xl font-semibold tabular-nums" :class="{ 'text-red-600 dark:text-red-400': Number(s.saldo) < 0 }">
              {{ s.saldo === null ? "–" : `${betrag(s.saldo)} ${waehrung(s.waehrung)}` }}
            </div>
            <template v-if="s.vorgemerkt !== null && s.saldo !== null">
              <div
                v-tooltip.left="'Noch nicht gebucht; im Kontostand der Bank noch nicht enthalten'"
                class="text-sm text-muted-color tabular-nums"
              >
                {{ s.anzahl }} vorgemerkt: {{ betrag(s.vorgemerkt) }} {{ waehrung(s.waehrung) }}
              </div>
              <div class="text-sm font-medium tabular-nums" :class="{ 'text-red-600 dark:text-red-400': danach(s.saldo, s.vorgemerkt) < 0 }">
                abzüglich vorgemerkt: {{ betrag(danach(s.saldo, s.vorgemerkt)) }} {{ waehrung(s.waehrung) }}
              </div>
            </template>
          </div>
          <input ref="dateiFeld" type="file" accept=".xml,application/xml,text/xml" multiple hidden @change="einlesen" />
          <Button
            v-tooltip.bottom="'Alte Kontoauszüge als CAMT.053 einlesen, z. B. aus MoneyMoney (Ablage → Exportieren)'"
            icon="pi pi-upload"
            label="Auszüge einlesen"
            size="small"
            severity="secondary"
            text
            :loading="liestEin"
            @click="dateiFeld?.click()"
          />
          <Button
            v-tooltip.bottom="
              'Ruft alle Konten ab; mit dir als Auslöser gilt das Limit von ' +
              `${daten?.abrufe_je_tag ?? 4} Abrufen in 24 Stunden nicht` +
              (mitIng ? ', die ING-Konten kommen über FinTS' : '')
            "
            icon="pi pi-refresh"
            label="Jetzt abrufen"
            size="small"
            severity="secondary"
            :loading="ruftAb"
            :disabled="!mitIng && !daten?.eingerichtet"
            @click="abrufen"
          />
        </div>
      </div>

      <Message v-if="mitIng && daten?.depot.abgelehnt && (!gewaehlt || gewaehlt.ing)" severity="error" class="mb-3">
        {{ daten.depot.fehler }}
      </Message>
      <Message v-else-if="mitIng && daten?.depot.freigabe && (!gewaehlt || gewaehlt.ing)" severity="warn" class="mb-3">
        Die ING wartet auf eine Freigabe in der App. Automatisch fragt der Abruf nicht erneut nach; einmal „Jetzt
        abrufen“ und in der ING-App bestätigen.
      </Message>
      <Message v-if="gewaehlt && warnung(gewaehlt)" severity="warn" class="mb-3">
        {{ warnung(gewaehlt) }}<template v-if="gewaehlt.freigabe_tage !== null && gewaehlt.freigabe_tage <= 14">
          · erneuern im Container: <code>finanz-o-mat bank verbinden --bank {{ gewaehlt.bank }}</code></template
        >
      </Message>

      <div v-if="verlaufZeigen" class="mb-3">
        <VerlaufChart
          :punkte="verlaufPunkte"
          :farbe="gewaehlt?.farbe"
          :waehrung="verlauf?.waehrung"
          :titel="gewaehlt ? `Kontostand ${gewaehlt.name}` : 'Kontostand aller Konten'"
        >
          <template #leer>
            {{ verlauf === null ? "Verlauf wird geladen …" : "Für diesen Zeitraum gibt es noch keinen Verlauf." }}
          </template>
        </VerlaufChart>
      </div>

      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex flex-wrap items-center gap-2">
          <DatePicker
            v-model="zeitraum"
            selection-mode="range"
            :manual-input="false"
            date-format="dd.mm.yy"
            placeholder="Zeitraum"
            show-icon
            icon-display="input"
            show-button-bar
            size="small"
            class="w-60"
            aria-label="Zeitraum"
          />
          <SelectButton
            v-model="schnell"
            :options="[...schnellOptionen]"
            option-label="label"
            option-value="value"
            size="small"
            aria-label="Schnellauswahl Zeitraum"
          />
        </div>
        <div class="flex items-center gap-2">
          <Button
            v-tooltip.top="verlaufZeigen ? 'Verlauf ausblenden' : 'Verlauf einblenden'"
            icon="pi pi-chart-line"
            size="small"
            :severity="verlaufZeigen ? undefined : 'secondary'"
            :text="!verlaufZeigen"
            :outlined="verlaufZeigen"
            aria-label="Verlauf"
            @click="verlaufZeigen = !verlaufZeigen"
          />
          <SuchFeld v-model="suche" :treffer="gefiltert.length" :gesamt="imZeitraumGefiltert.length" />
        </div>
      </div>
      <DataTable
        :value="gefiltert"
        :loading="laedt"
        size="small"
        data-key="pruefsumme"
        scrollable
        scroll-height="flex"
        class="lg:min-h-0 lg:flex-1"
        :row-class="(b: KontoBuchung) => (b.vorgemerkt ? 'vorgemerkt' : '')"
        paginator
        :rows="100"
      >
        <template #empty>
          <span class="text-muted-color">{{ vonIso || bisIso ? "Keine Umsätze in diesem Zeitraum." : "Keine Umsätze." }}</span>
        </template>
        <Column header="Datum" class="w-28 whitespace-nowrap">
          <template #body="{ data }">
            {{ datum(data.buchungsdatum) }}
            <small
              v-if="data.vorgemerkt"
              v-tooltip.top="'Noch nicht gebucht. Kann sich bis zur Buchung ändern oder wegfallen.'"
              class="block text-muted-color"
              >vorgemerkt</small
            >
          </template>
        </Column>
        <Column v-if="!gewaehlt" header="Konto" class="w-36">
          <template #body="{ data }">
            <span class="flex items-center gap-1.5 text-sm">
              <span
                v-if="nachSchluessel[data.konto]?.farbe"
                class="h-2 w-2 shrink-0 rounded-full"
                :style="punkt(nachSchluessel[data.konto].farbe)"
              />
              {{ nachSchluessel[data.konto]?.name ?? data.konto }}
            </span>
          </template>
        </Column>
        <Column header="Empfänger / Verwendungszweck">
          <template #body="{ data }">
            <div class="font-medium">{{ data.partner || "–" }}</div>
            <small v-if="data.verwendungszweck && data.verwendungszweck !== data.partner" class="block text-muted-color">
              {{ data.verwendungszweck }}
            </small>
          </template>
        </Column>
        <Column header="Betrag" class="w-36 text-right">
          <template #body="{ data }">
            <div class="tabular-nums" :class="Number(data.betrag) < 0 ? 'text-red-600 dark:text-red-400' : 'text-green-700 dark:text-green-400'">
              {{ betrag(data.betrag) }} €
            </div>
            <small v-if="data.original_betrag" class="block text-muted-color tabular-nums">
              {{ betrag(data.original_betrag) }} {{ data.original_waehrung }}
            </small>
          </template>
        </Column>
      </DataTable>
    </section>
  </div>

  <Dialog
    :visible="!!umbenennen"
    modal
    :header="umbenennen?.depot ? 'Depot anpassen' : 'Konto anpassen'"
    class="w-[min(420px,calc(100vw-2rem))]"
    @update:visible="(v: boolean) => !v && (umbenennen = null)"
  >
    <form v-if="umbenennen" class="flex flex-col gap-3" @submit.prevent="nameSpeichern">
      <label class="flex flex-col gap-1 text-sm">
        Eigener Name
        <InputText v-model="umbenennen.name" fluid autofocus maxlength="60" :placeholder="umbenennen.bank" />
      </label>
      <small class="text-muted-color">
        Bleibt bei jedem Abruf erhalten. Leer lassen für den Namen der Bank („{{ umbenennen.bank }}“).
      </small>
      <div class="flex flex-col gap-2 text-sm">
        Farbe
        <div class="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Farbe">
          <button
            type="button"
            role="radio"
            :aria-checked="!umbenennen.farbe"
            v-tooltip.top="'Keine'"
            aria-label="Keine Farbe"
            class="flex h-7 w-7 items-center justify-center rounded-full border border-surface-300 dark:border-surface-600"
            :class="{ 'ring-2 ring-primary ring-offset-2 ring-offset-surface-0 dark:ring-offset-surface-900': !umbenennen.farbe }"
            @click="umbenennen.farbe = ''"
          >
            <i class="pi pi-ban text-xs text-muted-color" />
          </button>
          <button
            v-for="f in FARBEN"
            :key="f.wert"
            type="button"
            role="radio"
            :aria-checked="umbenennen.farbe === f.wert"
            v-tooltip.top="f.name"
            :aria-label="f.name"
            class="h-7 w-7 rounded-full"
            :class="{ 'ring-2 ring-primary ring-offset-2 ring-offset-surface-0 dark:ring-offset-surface-900': umbenennen.farbe === f.wert }"
            :style="{ background: f.wert }"
            @click="umbenennen.farbe = f.wert"
          />
          <label
            v-tooltip.top="'Eigene Farbe'"
            class="relative flex h-7 w-7 cursor-pointer items-center justify-center overflow-hidden rounded-full border border-surface-300 dark:border-surface-600"
            :class="{ 'ring-2 ring-primary ring-offset-2 ring-offset-surface-0 dark:ring-offset-surface-900': eigeneFarbe }"
            :style="eigeneFarbe ? { background: umbenennen.farbe } : undefined"
          >
            <i v-if="!eigeneFarbe" class="pi pi-palette text-xs text-muted-color" />
            <input
              type="color"
              aria-label="Eigene Farbe"
              class="absolute inset-0 cursor-pointer opacity-0"
              :value="umbenennen.farbe || '#3b82f6'"
              @input="umbenennen.farbe = ($event.target as HTMLInputElement).value"
            />
          </label>
        </div>
      </div>
      <div class="flex justify-end gap-2">
        <Button label="Abbrechen" severity="secondary" text @click="umbenennen = null" />
        <Button type="submit" label="Speichern" icon="pi pi-check" :loading="speichertName" />
      </div>
    </form>
  </Dialog>
</template>

<style scoped>
/* Kopierknöpfe neben IBAN und BIC: nicht höher als die Textzeile */
.kopieren {
  width: 1.5rem;
  height: 1.5rem;
  margin-left: 0.125rem;
}
/* Vorgemerkte Umsätze schraffiert wie in MoneyMoney */
:deep(tr.vorgemerkt) {
  background-image: repeating-linear-gradient(
    135deg,
    transparent 0 10px,
    color-mix(in srgb, var(--p-text-color) 7%, transparent) 10px 20px
  );
}
</style>
