import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// npm run dev -> http://localhost:5173 (API at http://localhost:8000, proxied)
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": process.env.API_URL || "http://localhost:8000" } },
  build: { chunkSizeWarningLimit: 1200 },
});
