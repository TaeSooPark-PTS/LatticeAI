import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import packageJson from "./package.json";

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(packageJson.version),
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "frontend/src"),
    },
  },
  test: {
    environment: "jsdom",
    include: ["frontend/src/**/*.test.{ts,tsx}"],
    setupFiles: ["frontend/src/test/setup.ts"],
    clearMocks: true,
    coverage: {
      provider: "v8",
      // `all` is the honest setting: without it the report covers only files a
      // test already imports, so a module with no test at all simply vanishes
      // from the denominator and the percentage flatters itself.
      all: true,
      include: ["frontend/src/**/*.{ts,tsx}"],
      exclude: [
        "frontend/src/**/*.test.{ts,tsx}",
        "frontend/src/test/**",
        "frontend/src/api/openapi.ts", // generated
        "frontend/src/i18n/*.ts", // copy tables, not logic
        "frontend/src/main.tsx", // bootstrap, covered by the visual suite
      ],
      reporter: ["text-summary", "text"],
    },
  },
});
