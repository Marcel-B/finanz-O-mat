<script setup lang="ts">
import IconField from "primevue/iconfield";
import InputIcon from "primevue/inputicon";
import InputText from "primevue/inputtext";

const suche = defineModel<string>({ default: "" });
defineProps<{ treffer?: number; gesamt?: number }>();
</script>

<template>
  <div class="flex items-center gap-2">
    <small v-if="suche.trim() && gesamt !== undefined" class="whitespace-nowrap text-muted-color">
      {{ treffer }} von {{ gesamt }}
    </small>
    <IconField>
      <InputIcon class="pi pi-search" />
      <InputText v-model="suche" type="search" size="small" placeholder="Suchen …" aria-label="Suchen" class="w-44 sm:w-56" />
    </IconField>
  </div>
</template>
