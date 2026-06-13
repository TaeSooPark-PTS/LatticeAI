import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const outRoot = path.resolve(repoRoot, "output/playwright/v4.5.0-product-surface-reset");
const phase = process.argv[2] || "after";
const port = Number(process.env.LTCAI_VISUAL_PORT || 4931);
const baseURL = `http://127.0.0.1:${port}`;

const screens = [
  ["01-first-run", "/app"],
  ["02-login-account", "/app#/account"],
  ["03-workspace-selection", "/app#/workspace-admin"],
  ["04-model-recommendation", "/app#/models"],
  ["05-brain-overview", "/app#/brain"],
  ["06-graph-explorer", "/app#/knowledge-graph"],
  ["07-ask", "/app#/chat"],
  ["08-capture-files", "/app#/files"],
  ["09-capture-folders", "/app#/my-computer"],
  ["10-capture-web", "/app#/capture"],
  ["11-capture-pipeline", "/app#/pipeline"],
  ["12-act-agents", "/app#/agents"],
  ["13-act-runs", "/app#/runs"],
  ["14-act-workflows", "/app#/workflows"],
  ["15-library-skills", "/app#/skills"],
  ["16-library-connections", "/app#/mcp"],
  ["17-library-marketplace", "/app#/marketplace"],
  ["18-system-settings", "/app#/settings"],
  ["19-system-snapshots", "/app#/snapshots"],
  ["20-system-admin", "/app#/admin/security"],
];

async function waitForServer() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseURL}/health`);
      if (res.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`mock server did not start at ${baseURL}`);
}

async function withServer(fn) {
  const server = spawn(process.execPath, ["tests/visual/mock_server.cjs"], {
    cwd: repoRoot,
    env: { ...process.env, LTCAI_VISUAL_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  server.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  try {
    await waitForServer();
    await fn();
  } finally {
    server.kill();
    if (stderr.trim()) {
      await fs.writeFile(path.join(outRoot, `${phase}-mock-server.stderr.log`), stderr);
    }
  }
}

async function main() {
  const screenshotDir = path.join(outRoot, "screenshots", phase);
  const videoDir = path.join(outRoot, "videos", phase);
  await fs.mkdir(screenshotDir, { recursive: true });
  await fs.mkdir(videoDir, { recursive: true });

  await withServer(async () => {
    const browser = await chromium.launch();
    const context = await browser.newContext({
      baseURL,
      viewport: { width: 1440, height: 1000 },
      recordVideo: { dir: videoDir, size: { width: 1440, height: 1000 } },
    });
    const page = await context.newPage();

    for (const [name, route] of screens) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      await page.waitForSelector("main h1, main h2", { timeout: 10000 });
      await page.screenshot({ path: path.join(screenshotDir, `${name}.png`), fullPage: true });
    }

    await page.goto("/app");
    await page.waitForSelector("text=Lattice AI");
    for (const route of ["/app#/models", "/app#/knowledge-graph", "/app#/chat", "/app#/files", "/app#/settings"]) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(350);
    }
    const video = page.video();
    await context.close();
    if (video) {
      await video.saveAs(path.join(videoDir, `${phase}-product-walkthrough.webm`));
    }
    await browser.close();
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
