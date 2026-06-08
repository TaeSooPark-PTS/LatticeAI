#!/usr/bin/env node
/*
 * Capture v3.4.0 /app view screenshots into docs/assets/v3.4.0/.
 *
 * Drives the real SPA (built hashed assets) against the visual mock server, so
 * every screenshot is the genuine v3.4.0 frontend rendering real view code with
 * representative-but-honest mock data. Live-model output (VLM inference, agent
 * LLM text) is NOT simulated here; those remain runtime-pending per the release
 * notes. Run `npm run build:assets` first so the manifest points at new code.
 *
 *   node scripts/capture/capture_v340.js
 * Env: LTCAI_CAPTURE_BASE_URL (default http://127.0.0.1:4927 — the mock server)
 */
const fs = require("fs");
const path = require("path");

async function loadPlaywright() {
  try { return require("@playwright/test"); } catch (_) { return require("playwright"); }
}

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(ROOT, "docs", "assets", "v3.4.0");
const BASE = process.env.LTCAI_CAPTURE_BASE_URL || "http://127.0.0.1:4927";

// [hash route, output filename, theme]. Selector is the common view header
// (.lt3-vhead) except Chat, which is a flush layout (.lt3-chat).
const SHOTS = [
  ["home", "home.png", "light"],
  ["chat", "chat.png", "light"],
  ["files", "files.png", "light"],
  ["knowledge-graph", "knowledge-graph.png", "light"],
  ["memory", "memory.png", "light"],
  ["agents", "agents.png", "light"],
  ["agents", "agent-run.png", "light"],
  ["workflows", "workflows.png", "light"],
  ["settings", "settings.png", "light"],
  ["my-computer", "local-agent.png", "light"],
  ["my-computer", "connect-folder.png", "light"],
  ["chat", "vision-input.png", "light"],
  ["hooks", "hooks-dispatch.png", "light"],
];

async function prepare(page, theme) {
  await page.addInitScript((mode) => {
    localStorage.setItem("lt-theme", mode);
    localStorage.setItem("ltcai_mode", "admin");
    localStorage.setItem("ltcai_user_email", "demo@lattice.local");
    localStorage.setItem("ltcai_is_admin", "true");
  }, theme);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = await loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1480, height: 940 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();
  await prepare(page, "light");
  await page.goto(new URL("/app#/home", BASE).toString(), { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("#app .lt3-shell, #app .lt3-vhead, #app", { timeout: 20000 }).catch(() => {});

  for (const [routeKey, filename, theme] of SHOTS) {
    await page.evaluate((mode) => document.documentElement.setAttribute("data-lt-theme", mode), theme);
    await page.evaluate((key) => { location.hash = "#/" + key; }, routeKey);
    // Wait for the view to mount: header for normal views, .lt3-chat for chat.
    await page.waitForSelector("#app .lt3-vhead, #app .lt3-chat", { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(Number(process.env.LTCAI_CAPTURE_SETTLE_MS || 1200));
    const out = path.join(OUT, filename);
    await page.screenshot({ path: out, fullPage: false });
    console.log(out);
  }

  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
