import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Прокси на /api нужен только для `npm run dev`: в проде nginx делает то же
// самое перед статикой. Адрес бэкенда переопределяется через .env
// (VITE_API_PROXY_TARGET) — по умолчанию локальный uvicorn на 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
