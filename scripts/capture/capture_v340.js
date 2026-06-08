#!/usr/bin/env node
/*
 * Capture v3.4.0 /app view screenshots into docs/assets/v3.4.0/.
 *
 * Drives the real SPA (built hashed assets) against the visual mock server, so
 * every screenshot is the genuine v3.4.0 frontend rendering real view code with
 * representative-but-honest mock data. Live-model output (VLM inference, agent
 * LLM text) is NOT simulated; those remain runtime-pending per the release notes.
 * Run `npm run build:assets` first so the manifest points at the new code.
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

// { route, file, [action] }. action: "agent-run" clicks Run and waits for logs;
// "scroll-bottom" scrolls the view to reveal lower panels before the shot.
const SHOTS = [
  { route: "home", file: "home.png" },
  { route: "chat", file: "chat.png" },
  { route: "chat", file: "vision-input.png" },
  { route: "files", file: "files.png" },
  { route: "files", file: "connect-folder.png", action: "scroll-bottom" },
  { route: "knowledge-graph", file: "knowledge-graph.png" },
  { route: "memory", file: "memory.png" },
  { route: "agents", file: "agents.png" },
  { route: "agents", file: "agent-run.png", action: "agent-run" },
  { route: "workflows", file: "workflows.png" },
  { route: "settings", file: "settings.png" },
  { route: "my-computer", file: "local-agent.png" },
  { route: "hooks", file: "hooks-dispatch.png", action: "scroll-bottom" },
];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { chromium } = await loadPlaywright();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1480, height: 940 }, deviceScaleFactor: 2 });
  await context.addInitScript(() => {
    localStorage.setItem("lt-theme", "light");
    localStorage.setItem("ltcai_mode", "admin");
    localStorage.setItem("ltcai_user_email", "demo@lattice.local");
    localStorage.setItem("ltcai_is_admin", "true");
  });
  const page = await context.newPage();

  for (const shot of SHOTS) {
    // Fresh full-page load per shot — avoids transient cross-view overlap that a
    // same-page hash change can leave behind, so headers render crisp.
    await page.goto(new URL("/app#/" + shot.route, BASE).toString(), { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.evaluate((mode) => document.documentElement.setAttribute("data-lt-theme", mode), "light");
    await page.waitForSelector("#app .lt3-vhead, #app .lt3-chat", { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1800);  // let the view hydrate fully (crisp, not mid-load)

    if (shot.action === "agent-run") {
      // Fill the goal and trigger a real run; wait for the logs/timeline to render.
      const ta = page.locator("#app textarea").first();
      await ta.fill("Summarize this week's release work and propose next steps.").catch(() => {});
      const runBtn = page.getByRole("button", { name: /run agents/i }).first();
      await runBtn.click({ timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(1500);
      await page.evaluate(() => { const a = document.querySelector(".lt3-view"); if (a) a.scrollTop = 0; });
    } else if (shot.action === "scroll-bottom") {
      await page.evaluate(() => {
        const sc = document.querySelector(".lt3-view") || document.scrollingElement;
        if (sc) sc.scrollTop = sc.scrollHeight;
      });
      await page.waitForTimeout(700);
    }

    const out = path.join(OUT, shot.file);
    await page.screenshot({ path: out, fullPage: false });
    console.log(out);
  }

  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
