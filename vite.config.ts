import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import packageJson from "./package.json";

const API_TARGET = process.env.LTCAI_API_TARGET || "http://127.0.0.1:4825";

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(packageJson.version),
  },
  root: "frontend",
  base: "/static/app/",
  plugins: [
    react(),
    {
      name: "theme-boot-dev-path-fallback",
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          if (req.url?.startsWith("/static/app/static/app/theme-boot.js")) {
            req.url = req.url.replace("/static/app/static/app/theme-boot.js", "/static/app/theme-boot.js");
          }
          next();
        });
      },
    },
  ],
  build: {
    outDir: "../static/app",
    emptyOutDir: true,
    // No production sourcemaps: they leak source and bloat the shipped payload.
    // Use `vite build --sourcemap` locally when debugging a release build.
    sourcemap: false,
    manifest: "asset-manifest.json",
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": API_TARGET,
      "/account": API_TARGET,
      "/agents": API_TARGET,
      "/automation": API_TARGET,
      "/auth": API_TARGET,
      "/chat": API_TARGET,
      "/cu": API_TARGET,
      "/engines": API_TARGET,
      "/garden": API_TARGET,
      "/health": API_TARGET,
      "/history": API_TARGET,
      "/invitations": API_TARGET,
      "/knowledge-graph": API_TARGET,
      "/local": API_TARGET,
      "/login": API_TARGET,
      "/logout": API_TARGET,
      "/marketplace": API_TARGET,
      "/mcp": API_TARGET,
      "/models": API_TARGET,
      "/network": API_TARGET,
      "/permissions": API_TARGET,
      "/plugins": API_TARGET,
      "/realtime": API_TARGET,
      "/register": API_TARGET,
      "/setup": API_TARGET,
      "/tools": API_TARGET,
      "/upload": API_TARGET,
      "/vpc": API_TARGET,
      "/workspace": API_TARGET,
      "/workflows": API_TARGET,
    },
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "frontend/src"),
    },
  },
});
