import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Der Build landet in ustva/static und wird dort vom FastAPI-Backend ausgeliefert.
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: fileURLToPath(new URL("../ustva/static", import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    // Entwicklung: npm run dev, Backend parallel mit python -m ustva serve
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
