import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  root: "frontend",
  base: "/static/app/",
  plugins: [react()],
  build: {
    outDir: "../static/app",
    emptyOutDir: true,
    sourcemap: true,
    manifest: "asset-manifest.json",
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/agents": "http://127.0.0.1:8765",
      "/auth": "http://127.0.0.1:8765",
      "/chat": "http://127.0.0.1:8765",
      "/cu": "http://127.0.0.1:8765",
      "/engines": "http://127.0.0.1:8765",
      "/garden": "http://127.0.0.1:8765",
      "/health": "http://127.0.0.1:8765",
      "/history": "http://127.0.0.1:8765",
      "/invitations": "http://127.0.0.1:8765",
      "/knowledge-graph": "http://127.0.0.1:8765",
      "/local": "http://127.0.0.1:8765",
      "/login": "http://127.0.0.1:8765",
      "/logout": "http://127.0.0.1:8765",
      "/marketplace": "http://127.0.0.1:8765",
      "/mcp": "http://127.0.0.1:8765",
      "/models": "http://127.0.0.1:8765",
      "/network": "http://127.0.0.1:8765",
      "/permissions": "http://127.0.0.1:8765",
      "/plugins": "http://127.0.0.1:8765",
      "/realtime": "http://127.0.0.1:8765",
      "/register": "http://127.0.0.1:8765",
      "/setup": "http://127.0.0.1:8765",
      "/tools": "http://127.0.0.1:8765",
      "/upload": "http://127.0.0.1:8765",
      "/vpc": "http://127.0.0.1:8765",
      "/workspace": "http://127.0.0.1:8765",
      "/workflows": "http://127.0.0.1:8765",
    },
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "frontend/src"),
    },
  },
});
