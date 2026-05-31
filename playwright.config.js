const { defineConfig } = require("@playwright/test");

const port = Number(process.env.LTCAI_VISUAL_PORT || 4927);
const baseURL = process.env.LTCAI_VISUAL_BASE_URL || `http://127.0.0.1:${port}`;

module.exports = defineConfig({
  testDir: "tests/visual",
  timeout: 30_000,
  reporter: [["list"], ["html", { outputFolder: "playwright-report/visual", open: "never" }]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 920 },
    deviceScaleFactor: 1,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `LTCAI_VISUAL_PORT=${port} node tests/visual/mock_server.cjs`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 15_000,
  },
});
