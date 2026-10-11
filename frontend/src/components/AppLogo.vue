<script setup lang="ts">
import { useId } from "vue";

// Logo „Prozent-O“: das O aus dem Namen als Prozentzeichen der Umsatzsteuer (gleiches Motiv wie public/favicon.svg)
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
    <circle cx="22" cy="22" r="9.5" fill="none" stroke="#fff" stroke-width="6" />
    <path d="M49 16 L20 49" stroke="#fff" stroke-width="6" stroke-linecap="round" />
    <circle cx="45" cy="45" r="6.5" fill="#fff" />
  </svg>
</template>
