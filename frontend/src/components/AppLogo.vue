<script setup lang="ts">
import { useId } from "vue";

// Logo: Beleg mit Haken (gleiches Motiv wie public/favicon.svg)
withDefaults(defineProps<{ groesse?: number }>(), { groesse: 44 });

// Eigene Verlaufs-ID je Logo, falls mehrere auf der Seite stehen
const verlauf = useId();
</script>

<template>
  <svg :width="groesse" :height="groesse" viewBox="0 0 64 64" role="img" aria-label="Finanz-O-Mat" class="shrink-0">
    <defs>
      <linearGradient :id="verlauf" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#60a5fa" />
        <stop offset="1" stop-color="#1d4ed8" />
      </linearGradient>
    </defs>
    <rect width="64" height="64" rx="15" :fill="`url(#${verlauf})`" />
    <path
      d="M17 11 H47 V53 L42 49 L37 53 L32 49 L27 53 L22 49 L17 53 Z"
      fill="#fff"
      stroke="#fff"
      stroke-width="2"
      stroke-linejoin="round"
    />
    <rect x="22" y="18" width="20" height="3.5" rx="1.75" fill="#bfdbfe" />
    <rect x="22" y="25" width="13" height="3.5" rx="1.75" fill="#bfdbfe" />
    <circle cx="32" cy="38" r="7.5" fill="#2563eb" />
    <path
      d="M28.6 38.2 L31 40.6 L35.6 35.8"
      fill="none"
      stroke="#fff"
      stroke-width="2.6"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>
