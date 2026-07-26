import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Senza un target esplicito il minificatore riscrive "max-width: 820px" nella
    // sintassi a intervalli "(width <= 820px)", che Safari capisce solo dalla 16.4:
    // su un telefono piu vecchio l'intero layout mobile verrebbe ignorato.
    cssTarget: ["chrome100", "safari15", "firefox100", "edge100"],
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
