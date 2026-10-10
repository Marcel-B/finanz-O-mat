<script setup lang="ts">
import { computed, ref } from "vue";

import type { Monat } from "@/api";
import { betrag } from "@/format";

const props = defineProps<{ monate: Monat[] }>();

const B = 760; // viewBox-Breite
const H = 260;
const rand = { links: 64, rechts: 8, oben: 12, unten: 28 };
const innenB = B - rand.links - rand.rechts;
const innenH = H - rand.oben - rand.unten;
const band = computed(() => innenB / Math.max(props.monate.length, 1));
const balken = computed(() => Math.min(18, band.value * 0.3));

// "Runde" Obergrenze für die Achse (1, 2 oder 5 mal Zehnerpotenz je Schritt)
const skala = computed(() => {
  const max = Math.max(0, ...props.monate.flatMap((m) => [Number(m.einnahmen), Number(m.ausgaben)]));
  if (max <= 0) return { max: 1000, schritte: [0, 250, 500, 750, 1000] };
  const roh = max / 4;
  const p = 10 ** Math.floor(Math.log10(roh));
  const schritt = [1, 2, 5, 10].map((f) => f * p).find((s) => s >= roh) ?? 10 * p;
  const oben = Math.ceil(max / schritt) * schritt;
  const schritte = [];
  for (let v = 0; v <= oben + 1e-9; v += schritt) schritte.push(v);
  return { max: oben, schritte };
});

const y = (wert: number) => rand.oben + innenH - (Math.max(0, wert) / skala.value.max) * innenH;
const basis = rand.oben + innenH;

// Balken mit 4px gerundetem oberen Ende, unten bündig auf der Grundlinie
function pfad(x: number, wert: number): string {
  const w = balken.value;
  const top = y(wert);
  const h = basis - top;
  if (h <= 0) return "";
  const r = Math.min(4, h, w / 2);
  return `M${x},${basis} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${basis} Z`;
}

const kurz = (m: Monat) => m.monat.slice(0, 3);
const achse = (v: number) => (v >= 1000 ? `${betrag(v / 1000, true)} T€` : `${betrag(v, true)} €`);

const aktiv = ref<number | null>(null);
// Tooltip neben dem Monat, damit er die Balken nicht verdeckt; in der rechten Hälfte links davon
const tooltipStil = computed(() => {
  if (aktiv.value == null) return {};
  const rechts = aktiv.value >= props.monate.length / 2;
  const x = rand.links + band.value * (rechts ? aktiv.value : aktiv.value + 1);
  return { left: `${(x / B) * 100}%`, transform: rechts ? "translateX(calc(-100% - 4px))" : "translateX(4px)" };
});
</script>

<template>
  <div>
    <div class="mb-2 flex gap-4 text-sm">
      <span class="flex items-center gap-1">
        <span class="inline-block h-3 w-3 rounded-sm" style="background: var(--chart-einnahmen)" />Einnahmen
      </span>
      <span class="flex items-center gap-1">
        <span class="inline-block h-3 w-3 rounded-sm" style="background: var(--chart-ausgaben)" />Ausgaben
      </span>
    </div>
    <div class="relative" @mouseleave="aktiv = null">
      <svg :viewBox="`0 0 ${B} ${H}`" class="block h-auto w-full" role="img" aria-label="Einnahmen und Ausgaben je Monat">
        <g class="text-surface-200 dark:text-surface-700">
          <line
            v-for="s in skala.schritte"
            :key="s"
            :x1="rand.links"
            :x2="B - rand.rechts"
            :y1="y(s)"
            :y2="y(s)"
            stroke="currentColor"
            stroke-width="1"
          />
        </g>
        <g class="fill-surface-500 dark:fill-surface-400" font-size="11">
          <text v-for="s in skala.schritte" :key="s" :x="rand.links - 8" :y="y(s) + 4" text-anchor="end">{{ achse(s) }}</text>
          <text
            v-for="(m, i) in monate"
            :key="m.zeitraum"
            :x="rand.links + band * (i + 0.5)"
            :y="H - 8"
            text-anchor="middle"
          >{{ kurz(m) }}</text>
        </g>
        <g v-for="(m, i) in monate" :key="m.zeitraum">
          <rect
            v-if="aktiv === i"
            :x="rand.links + band * i"
            :y="rand.oben"
            :width="band"
            :height="innenH"
            class="fill-surface-100 dark:fill-surface-800"
          />
          <path :d="pfad(rand.links + band * (i + 0.5) - balken - 1, Number(m.einnahmen))" fill="var(--chart-einnahmen)" />
          <path :d="pfad(rand.links + band * (i + 0.5) + 1, Number(m.ausgaben))" fill="var(--chart-ausgaben)" />
          <!-- Trefferfläche größer als die Balken -->
          <rect
            :x="rand.links + band * i"
            :y="rand.oben"
            :width="band"
            :height="innenH + rand.unten"
            fill="transparent"
            @mouseenter="aktiv = i"
            @focus="aktiv = i"
          />
        </g>
      </svg>
      <div
        v-if="aktiv !== null"
        class="pointer-events-none absolute top-2 z-10 min-w-44 rounded-lg border border-surface-200 bg-surface-0 p-2 text-sm shadow-md dark:border-surface-700 dark:bg-surface-900"
        :style="tooltipStil"
      >
        <div class="mb-1 font-semibold">{{ monate[aktiv].monat }}</div>
        <div class="flex justify-between gap-4">
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-sm" style="background: var(--chart-einnahmen)" />Einnahmen
          </span>
          <span class="zahl">{{ betrag(monate[aktiv].einnahmen) }} €</span>
        </div>
        <div class="flex justify-between gap-4">
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-sm" style="background: var(--chart-ausgaben)" />Ausgaben
          </span>
          <span class="zahl">{{ betrag(monate[aktiv].ausgaben) }} €</span>
        </div>
        <div class="mt-1 flex justify-between gap-4 border-t border-surface-200 pt-1 dark:border-surface-700">
          <span>Ergebnis</span><span class="zahl font-semibold">{{ betrag(monate[aktiv].ergebnis) }} €</span>
        </div>
      </div>
    </div>
  </div>
</template>
