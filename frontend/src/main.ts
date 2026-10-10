import "primeicons/primeicons.css";
import "./style.css";

import Aura from "@primeuix/themes/aura";
import PrimeVue from "primevue/config";
import ConfirmationService from "primevue/confirmationservice";
import ToastService from "primevue/toastservice";
import Tooltip from "primevue/tooltip";
import { createApp } from "vue";

import App from "./App.vue";
import { deutsch } from "./locale";

createApp(App)
  .use(PrimeVue, {
    locale: deutsch,
    theme: {
      preset: Aura,
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
