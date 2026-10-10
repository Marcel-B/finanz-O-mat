<script setup lang="ts">
import Button from "primevue/button";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Password from "primevue/password";
import Select from "primevue/select";
import Tag from "primevue/tag";
import { useToast } from "primevue/usetoast";
import { computed, onMounted, reactive, ref } from "vue";

import { api, type Einstellungen, type Herkunft } from "@/api";

import KarteBox from "./KarteBox.vue";
import VersionKarte from "./VersionKarte.vue";

const emit = defineEmits<{ geaendert: [] }>();
const toast = useToast();

const daten = ref<Einstellungen | null>(null);
const form = reactive({
  paperless_url: "",
  paperless_token: "",
  paperless_tags_ausgang: "",
  paperless_tags_eingang: "",
  paperless_tag_kontoauszug: "",
  paperless_tag_protokoll: "",
  eigene_ustid: "",
  bundesland: "",
});
const zugang = reactive({ benutzer: "", aktuell: "", neu: "", wiederholung: "" });
const speichert = ref(false);
const testet = ref(false);
const testErgebnis = ref<{ ok: boolean; text: string } | null>(null);
const passwortFehler = ref("");
const passwortGeaendert = ref(false);

function uebernehmen(e: Einstellungen) {
  daten.value = e;
  form.paperless_url = e.paperless_url.wert;
  form.paperless_token = "";
  form.paperless_tags_ausgang = e.paperless_tags_ausgang.wert;
  form.paperless_tags_eingang = e.paperless_tags_eingang.wert;
  form.paperless_tag_kontoauszug = e.paperless_tag_kontoauszug.wert;
  form.paperless_tag_protokoll = e.paperless_tag_protokoll.wert;
  form.eigene_ustid = e.eigene_ustid.wert;
  form.bundesland = e.bundesland.wert;
  zugang.benutzer = e.anmeldung.benutzer;
}

onMounted(async () => {
  try {
    uebernehmen(await api.einstellungen());
  } catch (e) {
    fehler(e);
  }
});

function fehler(e: unknown) {
  toast.add({ severity: "error", summary: "Fehler", detail: (e as Error).message, life: 6000 });
}

const herkunftText: Record<Herkunft, string> = { app: "in der App gespeichert", umgebung: "aus der Konfigurationsdatei", "": "" };

async function speichern() {
  const d = daten.value;
  if (!d) return;
  // Nur geänderte Felder senden, damit Werte aus der Konfigurationsdatei nicht ungewollt in die App wandern.
  const aenderungen: Record<string, string> = {};
  const felder = [
    "paperless_url", "paperless_tags_ausgang", "paperless_tags_eingang", "paperless_tag_kontoauszug",
    "paperless_tag_protokoll", "eigene_ustid", "bundesland",
  ] as const;
  for (const k of felder) {
    if (form[k] !== d[k].wert) aenderungen[k] = form[k] ?? "";
  }
  if (form.paperless_token) aenderungen.paperless_token = form.paperless_token;
  if (!Object.keys(aenderungen).length) {
    toast.add({ severity: "info", summary: "Nichts geändert", life: 3000 });
    return;
  }
  speichert.value = true;
  try {
    uebernehmen(await api.einstellungenSpeichern(aenderungen));
    testErgebnis.value = null;
    toast.add({ severity: "success", summary: "Einstellungen gespeichert", life: 3000 });
    emit("geaendert");
  } catch (e) {
    fehler(e);
  } finally {
    speichert.value = false;
  }
}

async function tokenEntfernen() {
  try {
    uebernehmen(await api.einstellungenSpeichern({ paperless_token: "" }));
    emit("geaendert");
  } catch (e) {
    fehler(e);
  }
}

async function testen() {
  testet.value = true;
  testErgebnis.value = null;
  try {
    const r = await api.paperlessTesten({
      paperless_url: form.paperless_url || undefined,
      paperless_token: form.paperless_token || undefined,
    });
    testErgebnis.value = { ok: true, text: r.text };
  } catch (e) {
    testErgebnis.value = { ok: false, text: (e as Error).message };
  } finally {
    testet.value = false;
  }
}

const minLaenge = computed(() => daten.value?.anmeldung.min_laenge ?? 10);

async function passwortSpeichern() {
  passwortFehler.value = "";
  if (zugang.neu.length < minLaenge.value) {
    passwortFehler.value = `Das neue Passwort muss mindestens ${minLaenge.value} Zeichen haben.`;
    return;
  }
  if (zugang.neu !== zugang.wiederholung) {
    passwortFehler.value = "Die beiden neuen Passwörter stimmen nicht überein.";
    return;
  }
  try {
    await api.passwortAendern({ aktuell: zugang.aktuell, benutzer: zugang.benutzer, neu: zugang.neu });
    passwortGeaendert.value = true;
    // Der Browser hat noch die alten Zugangsdaten; nach dem Neuladen fragt er nach den neuen.
    setTimeout(() => window.location.reload(), 2500);
  } catch (e) {
    passwortFehler.value = (e as Error).message;
  }
}
</script>

<template>
  <div v-if="daten" class="grid gap-4 lg:grid-cols-2">
    <KarteBox titel="Paperless-ngx">
      <form class="flex flex-col gap-3" @submit.prevent="speichern">
        <label class="flex flex-col gap-1 text-sm">
          <span class="flex items-center gap-2">
            URL
            <small v-if="daten!.paperless_url.quelle" class="text-muted-color">{{ herkunftText[daten!.paperless_url.quelle] }}</small>
          </span>
          <InputText v-model="form.paperless_url" placeholder="http://192.168.1.20:8000" fluid />
        </label>
        <label class="flex flex-col gap-1 text-sm">
          <span class="flex items-center gap-2">
            API-Token
            <Tag
              v-if="daten!.paperless_token.gesetzt"
              severity="success"
              icon="pi pi-check"
              :value="daten!.paperless_token.quelle === 'app' ? 'in der App gespeichert' : 'aus der Konfigurationsdatei'"
            />
          </span>
          <Password
            v-model="form.paperless_token"
            :feedback="false"
            toggle-mask
            fluid
            autocomplete="off"
            :placeholder="daten!.paperless_token.gesetzt ? 'leer lassen, um das gespeicherte Token zu behalten' : 'Token einfügen'"
          />
          <span class="text-xs text-muted-color">
            In Paperless unter Profil → API-Token erzeugen. Das Token wird nie wieder angezeigt.
          </span>
        </label>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm">
            Tags Ausgangsrechnungen
            <InputText v-model="form.paperless_tags_ausgang" fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Tags Eingangsrechnungen
            <InputText v-model="form.paperless_tags_eingang" fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Tag Kontoauszug
            <InputText v-model="form.paperless_tag_kontoauszug" fluid />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Tag ELSTER-Protokoll
            <InputText v-model="form.paperless_tag_protokoll" fluid />
          </label>
        </div>
        <span class="-mt-1 text-xs text-muted-color">
          Kontoauszug und ELSTER-Übertragungsprotokoll landen über diese Tags im Monats-ZIP für die Steuerberatung.
          Titel oder Text müssen den Monat nennen, z. B. „09/2026“ oder „September 2026“.
        </span>
        <Message v-if="testErgebnis" :severity="testErgebnis.ok ? 'success' : 'error'" size="small">
          {{ testErgebnis.text }}
        </Message>
        <div class="flex flex-wrap gap-2">
          <Button type="submit" label="Speichern" icon="pi pi-save" :loading="speichert" />
          <Button label="Verbindung testen" icon="pi pi-bolt" severity="secondary" :loading="testet" @click="testen" />
          <span class="flex-1" />
          <Button
            v-if="daten!.paperless_token.quelle === 'app'"
            label="Token aus der App entfernen"
            severity="danger"
            text
            @click="tokenEntfernen"
          />
        </div>
      </form>
    </KarteBox>

    <KarteBox titel="Anmeldung">
      <Message v-if="passwortGeaendert" severity="success">
        Passwort geändert. Die Seite lädt gleich neu, dann fragt der Browser nach der neuen Anmeldung.
      </Message>
      <form v-else class="flex flex-col gap-3" @submit.prevent="passwortSpeichern">
        <p class="text-sm text-muted-color">
          <template v-if="daten!.anmeldung.aktiv">
            Angemeldet als <strong>{{ daten!.anmeldung.benutzer }}</strong>,
            Passwort {{ herkunftText[daten!.anmeldung.quelle] }}.
          </template>
          <template v-else>
            Die App ist noch ohne Passwort erreichbar. Mit einem Passwort fragt der Browser künftig nach der Anmeldung.
          </template>
        </p>
        <label class="flex flex-col gap-1 text-sm">
          Benutzername
          <InputText v-model="zugang.benutzer" autocomplete="username" fluid />
        </label>
        <label v-if="daten!.anmeldung.aktiv" class="flex flex-col gap-1 text-sm">
          Aktuelles Passwort
          <Password v-model="zugang.aktuell" :feedback="false" toggle-mask fluid autocomplete="current-password" />
        </label>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm">
            Neues Passwort
            <Password v-model="zugang.neu" :feedback="false" toggle-mask fluid autocomplete="new-password" />
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Wiederholen
            <Password v-model="zugang.wiederholung" :feedback="false" toggle-mask fluid autocomplete="new-password" />
          </label>
        </div>
        <span class="text-xs text-muted-color">
          Mindestens {{ minLaenge }} Zeichen. Gespeichert wird nur ein Hash. Passwort vergessen: im Container
          <code>python -m ustva passwort</code> ausführen.
        </span>
        <Message v-if="passwortFehler" severity="error" size="small">{{ passwortFehler }}</Message>
        <div>
          <Button type="submit" label="Passwort ändern" icon="pi pi-lock" />
        </div>
      </form>
    </KarteBox>

    <KarteBox titel="Allgemein">
      <form class="flex flex-col gap-3" @submit.prevent="speichern">
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="flex flex-col gap-1 text-sm">
            Eigene USt-IdNr.
            <InputText v-model="form.eigene_ustid" placeholder="DE123456789" fluid />
            <span class="text-xs text-muted-color">Erkennt, ob eine Rechnung von dir oder an dich ist.</span>
          </label>
          <label class="flex flex-col gap-1 text-sm">
            Bundesland
            <Select
              v-model="form.bundesland"
              :options="daten!.bundeslaender"
              option-label="name"
              option-value="wert"
              placeholder="nur bundesweite Feiertage"
              show-clear
              fluid
            />
            <span class="text-xs text-muted-color">Landesfeiertage verschieben die Abgabefrist.</span>
          </label>
        </div>
        <div>
          <Button type="submit" label="Speichern" icon="pi pi-save" :loading="speichert" />
        </div>
      </form>
    </KarteBox>

    <VersionKarte />
  </div>
</template>
