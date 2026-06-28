import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend talks to the Python analytics API (FastAPI, issue #40) over /api.
// In dev, Vite proxies /api to that backend so there are no CORS hoops.
// Override the target with ISREQ_API_URL when the backend runs elsewhere.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,            // bind 0.0.0.0 — reachable on the LAN
    port: 5173,
    allowedHosts: true,    // accept any Host header (LAN IP / hostname)
    proxy: {
      // /api is proxied server-side on this machine to the Django API on :8010.
      "/api": {
        target: process.env.WINDU_API_URL ?? "http://localhost:8010",
        changeOrigin: true,
      },
    },
  },
});
