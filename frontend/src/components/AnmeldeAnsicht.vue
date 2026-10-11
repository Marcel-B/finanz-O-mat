<script setup lang="ts">
import Button from "primevue/button";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Password from "primevue/password";
import { reactive, ref } from "vue";

import { api } from "@/api";
import AppLogo from "@/components/AppLogo.vue";

const emit = defineEmits<{ angemeldet: [] }>();

const zugang = reactive({ benutzer: "", passwort: "" });
const fehler = ref("");
const laeuft = ref(false);

async function anmelden() {
  fehler.value = "";
  laeuft.value = true;
  try {
    await api.anmelden(zugang.benutzer, zugang.passwort);
    zugang.passwort = "";
    emit("angemeldet");
  } catch (e) {
    fehler.value = (e as Error).message;
  } finally {
    laeuft.value = false;
  }
}
</script>

<template>
  <section
    class="mx-auto mt-8 max-w-sm rounded-xl border border-surface-200 bg-surface-0 p-6 dark:border-surface-700 dark:bg-surface-900"
  >
    <div class="mb-5 flex flex-col items-center gap-3">
      <AppLogo :groesse="64" />
      <h2 class="text-lg font-semibold">Anmelden</h2>
    </div>
    <form class="flex flex-col gap-3" @submit.prevent="anmelden">
      <label class="flex flex-col gap-1 text-sm">
        Benutzername
        <InputText v-model="zugang.benutzer" name="username" autocomplete="username" autofocus fluid />
      </label>
      <label class="flex flex-col gap-1 text-sm">
        Passwort
        <Password
          v-model="zugang.passwort"
          name="password"
          :feedback="false"
          toggle-mask
          fluid
          autocomplete="current-password"
        />
      </label>
      <Message v-if="fehler" severity="error" size="small">{{ fehler }}</Message>
      <Button type="submit" label="Anmelden" icon="pi pi-sign-in" :loading="laeuft" />
    </form>
  </section>
</template>
