const { defineConfig } = require("@playwright/test");

// Sidecar-backed E2E (Wave 3.2 residual). The runner
// (scripts/run_sidecar_e2e.mjs) starts a real FastAPI process and sets
// LTCAI_E2E_BASE_URL. This config does NOT start the visual mock server.
const baseURL = process.env.LTCAI_E2E_BASE_URL || "http://127.0.0.1:4899";

module.exports = defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { outputFolder: "playwright-report/e2e", open: "never" }]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 920 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
