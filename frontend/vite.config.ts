import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config — dev server on :5173, proxies /api to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // Force the browser (and any intermediate proxy) to always re-fetch
    // modules from the dev server. Without this, browsers may keep using
    // a cached copy of the JS bundle from before a code change — which is
    // exactly the behaviour that surfaces as "the new code isn't live".
    headers: {
      "Cache-Control": "no-store, no-cache, must-revalidate",
      Pragma: "no-cache",
      Expires: "0",
    },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
        // The /master endpoint renders up to 6 presets sequentially per
        // request; for a long track that can easily exceed the default
        // http-proxy timeout (~30s). Bump it to 10 minutes.
        proxyTimeout: 10 * 60 * 1000,
        timeout: 10 * 60 * 1000,
      },
    },
  },
});