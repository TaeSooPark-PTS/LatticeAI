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
      // 10.10.0 brought every file in `frontend/src` to 100 on all four
      // metrics. Thresholds turn that from a snapshot into a floor: a new
      // branch without a test fails here and in CI, the same run either way.
      // Note when reading the printed table: vitest hides fully-covered files
      // from the `text` reporter under an agent, so "my file vanished" means
      // 100, not missing — confirm with `--coverage.reporter=json-summary`.
      thresholds: {
        statements: 100,
        branches: 100,
        functions: 100,
        lines: 100,
      },
    },
  },
});
