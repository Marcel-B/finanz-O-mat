import "primeicons/primeicons.css";
import "./style.css";

import { definePreset } from "@primeuix/themes";
import Aura from "@primeuix/themes/aura";
import PrimeVue from "primevue/config";
import ConfirmationService from "primevue/confirmationservice";
import ToastService from "primevue/toastservice";
import Tooltip from "primevue/tooltip";
import { createApp } from "vue";

import App from "./App.vue";
import { deutsch } from "./locale";

// Primärfarbe Blau passend zum Logo und Marcels Firmenfarben (Aura hat standardmäßig Grün)
const blau = Object.fromEntries([50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((s) => [s, `{blue.${s}}`]));
const Thema = definePreset(Aura, { semantic: { primary: blau } });

createApp(App)
  .use(PrimeVue, {
    locale: deutsch,
    theme: {
      preset: Thema,
      options: {
        darkModeSelector: "system",
        // Tailwind-Utilities sollen PrimeVue-Stile überschreiben können
        cssLayer: { name: "primevue", order: "theme, base, primevue" },
      },
    },
  })
  .use(ToastService)
  .use(ConfirmationService)
  .directive("tooltip", Tooltip)
  .mount("#app");
