<script setup lang="ts">
defineProps<{ titel?: string; untertitel?: string }>();
</script>

<template>
  <section
    class="mb-4 min-w-0 rounded-xl border border-surface-200 bg-surface-0 p-4 dark:border-surface-700 dark:bg-surface-900"
  >
    <div v-if="titel || $slots.aktionen" class="mb-2 flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-lg font-semibold">
        {{ titel }}
        <small v-if="untertitel" class="ml-1 text-sm font-normal text-muted-color">{{ untertitel }}</small>
      </h2>
      <div class="flex flex-wrap gap-2"><slot name="aktionen" /></div>
    </div>
    <slot />
  </section>
</template>
