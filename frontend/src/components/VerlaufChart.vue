<script setup lang="ts">
// Verlauf von Kontostand oder Depotwert je Tag als Linie, mit Fadenkreuz und Wert beim Darüberfahren.
// Die Breite folgt dem Platz, damit Schrift und Linie nicht verzerren.
import { computed, onMounted, onUnmounted, ref } from "vue";

import type { VerlaufPunkt } from "@/api";
import { betrag, datum } from "@/format";

const props = withDefaults(
  defineProps<{ punkte: VerlaufPunkt[]; waehrung?: string; hoehe?: number; titel?: string; farbe?: string }>(),
  {
  waehrung: "EUR",
  hoehe: 150,
  titel: "Verlauf",
  },
);
// Farbe des Kontos, sonst die Diagrammfarbe der App
const linienfarbe = computed(() => props.farbe || "var(--chart-einnahmen)");

const kasten = ref<HTMLDivElement | null>(null);
const breite = ref(600);
let beobachter: ResizeObserver | null = null;
onMounted(() => {
  beobachter = new ResizeObserver(([e]) => (breite.value = Math.max(200, Math.round(e.contentRect.width))));
  if (kasten.value) beobachter.observe(kasten.value);
});
onUnmounted(() => beobachter?.disconnect());

const rand = { links: 64, rechts: 8, oben: 8, unten: 22 };
const innenB = computed(() => breite.value - rand.links - rand.rechts);
const innenH = computed(() => props.hoehe - rand.oben - rand.unten);

const TAG = 86_400_000;
const zeit = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
const daten = computed(() => props.punkte.map((p) => ({ tag: p.tag, t: zeit(p.tag), wert: Number(p.wert) })));
const anfang = computed(() => daten.value[0]?.t ?? 0);
const ende = computed(() => Math.max(daten.value.at(-1)?.t ?? 0, anfang.value + TAG));

// "Runde" Achse: 1, 2 oder 5 mal Zehnerpotenz je Schritt, etwa vier Linien
const skala = computed(() => {
  const werte = daten.value.map((p) => p.wert);
  let min = Math.min(...werte);
  let max = Math.max(...werte);
  if (min === max) {
    const rand = Math.abs(min) * 0.05 || 1;
    min -= rand;
    max += rand;
  }
  const roh = (max - min) / 4;
  const p = 10 ** Math.floor(Math.log10(roh));
  const schritt = [1, 2, 5, 10].map((f) => f * p).find((s) => s >= roh) ?? 10 * p;
  const unten = Math.floor(min / schritt) * schritt;
  const oben = Math.ceil(max / schritt) * schritt;
  const schritte: number[] = [];
  for (let v = unten; v <= oben + schritt / 1e6; v += schritt) schritte.push(Number(v.toPrecision(12)));
  return { min: unten, max: oben, schritte };
});

const x = (t: number) => rand.links + ((t - anfang.value) / (ende.value - anfang.value)) * innenB.value;
const y = (w: number) => rand.oben + innenH.value - ((w - skala.value.min) / (skala.value.max - skala.value.min)) * innenH.value;
const basis = computed(() => rand.oben + innenH.value);

const linie = computed(() => daten.value.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.wert).toFixed(1)}`).join(" "));
const flaeche = computed(() =>
  daten.value.length ? `${linie.value} L${x(daten.value.at(-1)!.t).toFixed(1)},${basis.value} L${x(daten.value[0].t).toFixed(1)},${basis.value} Z` : "",
);
const nulllinie = computed(() => (skala.value.min < 0 && skala.value.max > 0 ? y(0) : null));

// Beschriftung der Zeitachse: Monatsanfänge bei langen Zeiträumen, sonst gleichmäßig verteilte Tage
const MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
const zeitachse = computed(() => {
  const spanne = (ende.value - anfang.value) / TAG;
  const platz = Math.max(2, Math.floor(innenB.value / 70));
  const marken: { t: number; text: string }[] = [];
  if (spanne > 62) {
    const start = new Date(anfang.value);
    let j = start.getUTCFullYear();
    let m = start.getUTCMonth() + 1;
    const alle: { t: number; j: number; m: number }[] = [];
    for (;;) {
      if (m > 11) {
        m = 0;
        j += 1;
      }
      const t = Date.UTC(j, m, 1);
      if (t > ende.value) break;
      alle.push({ t, j, m });
      m += 1;
    }
    const jede = Math.max(1, Math.ceil(alle.length / platz));
    alle.forEach((a, i) => {
      if (i % jede === 0) marken.push({ t: a.t, text: a.m === 0 || i === 0 ? `${MONATE[a.m]} ${String(a.j).slice(2)}` : MONATE[a.m] });
    });
  } else {
    const jede = Math.max(1, Math.ceil(spanne / platz));
    for (let t = anfang.value; t <= ende.value; t += jede * TAG) {
      const d = new Date(t);
      marken.push({ t, text: `${d.getUTCDate()}.${d.getUTCMonth() + 1}.` });
    }
  }
  return marken;
});

// Alle Beschriftungen in derselben Einheit: ab 10.000 in Tausend
const tausend = computed(() => Math.max(...skala.value.schritte.map(Math.abs)) >= 10_000);
const achse = (v: number) => {
  const a = tausend.value ? `${betrag(v / 1000, true)} T` : betrag(v, true);
  return `${a} ${props.waehrung === "EUR" ? "€" : props.waehrung}`;
};

// Fadenkreuz: nächster Punkt zur Maus
const aktiv = ref<number | null>(null);
function bewegen(e: PointerEvent) {
  const svg = e.currentTarget as SVGSVGElement;
  const r = svg.getBoundingClientRect();
  const mx = e.clientX - r.left;
  const t = anfang.value + ((mx - rand.links) / innenB.value) * (ende.value - anfang.value);
  let beste = 0;
  daten.value.forEach((p, i) => {
    if (Math.abs(p.t - t) < Math.abs(daten.value[beste].t - t)) beste = i;
  });
  aktiv.value = daten.value.length ? beste : null;
}
const punkt = computed(() => (aktiv.value === null ? null : daten.value[aktiv.value]));
const tooltipStil = computed(() => {
  if (!punkt.value) return {};
  const px = x(punkt.value.t);
  const rechts = px > breite.value / 2;
  return { left: `${px}px`, transform: rechts ? "translateX(calc(-100% - 8px))" : "translateX(8px)" };
});
</script>

<template>
  <div ref="kasten" class="relative w-full" :style="{ height: `${hoehe}px` }">
    <div v-if="daten.length < 2" class="flex h-full items-center justify-center text-sm text-muted-color">
      <slot name="leer">Noch zu wenig Daten für einen Verlauf.</slot>
    </div>
    <svg
      v-else
      :width="breite"
      :height="hoehe"
      :viewBox="`0 0 ${breite} ${hoehe}`"
      class="block touch-none"
      role="img"
      :aria-label="titel"
      @pointermove="bewegen"
      @pointerdown="bewegen"
      @pointerleave="aktiv = null"
    >
      <defs>
        <linearGradient id="verlauf-flaeche" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" :stop-color="linienfarbe" stop-opacity="0.22" />
          <stop offset="100%" :stop-color="linienfarbe" stop-opacity="0.02" />
        </linearGradient>
      </defs>
      <g class="text-surface-200 dark:text-surface-700">
        <line
          v-for="s in skala.schritte"
          :key="s"
          :x1="rand.links"
          :x2="breite - rand.rechts"
          :y1="y(s)"
          :y2="y(s)"
          stroke="currentColor"
          stroke-width="1"
        />
      </g>
      <line
        v-if="nulllinie !== null"
        :x1="rand.links"
        :x2="breite - rand.rechts"
        :y1="nulllinie"
        :y2="nulllinie"
        class="stroke-surface-400 dark:stroke-surface-500"
        stroke-width="1"
      />
      <g class="fill-surface-500 dark:fill-surface-400" font-size="11">
        <text v-for="s in skala.schritte" :key="s" :x="rand.links - 8" :y="y(s) + 4" text-anchor="end" class="tabular-nums">
          {{ achse(s) }}
        </text>
        <text v-for="m in zeitachse" :key="m.t" :x="x(m.t)" :y="hoehe - 6" text-anchor="middle">{{ m.text }}</text>
      </g>
      <path :d="flaeche" fill="url(#verlauf-flaeche)" />
      <path :d="linie" fill="none" :stroke="linienfarbe" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
      <g v-if="punkt">
        <line :x1="x(punkt.t)" :x2="x(punkt.t)" :y1="rand.oben" :y2="basis" class="stroke-surface-400 dark:stroke-surface-500" stroke-width="1" />
        <circle
          :cx="x(punkt.t)"
          :cy="y(punkt.wert)"
          r="4"
          :fill="linienfarbe"
          class="stroke-surface-0 dark:stroke-surface-900"
          stroke-width="2"
        />
      </g>
    </svg>
    <div
      v-if="punkt"
      class="pointer-events-none absolute top-1 z-10 rounded-lg border border-surface-200 bg-surface-0 px-2 py-1 text-sm whitespace-nowrap shadow-md dark:border-surface-700 dark:bg-surface-900"
      :style="tooltipStil"
    >
      <div class="text-muted-color">{{ datum(punkt.tag) }}</div>
      <div class="font-semibold tabular-nums">{{ betrag(punkt.wert) }} {{ waehrung === "EUR" ? "€" : waehrung }}</div>
    </div>
  </div>
</template>
