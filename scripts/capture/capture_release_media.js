#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

async function loadPlaywright() {
  try {
    return require("@playwright/test");
  } catch (_) {
    return require("playwright");
  }
}

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(ROOT, "docs", "images");
const FRAMES = path.join(OUT, "tmp_frames", "release_221");
const BASE = process.env.LTCAI_CAPTURE_BASE_URL || "http://127.0.0.1:4825";

function ensureDirs() {
  fs.mkdirSync(OUT, { recursive: true });
  fs.rmSync(FRAMES, { recursive: true, force: true });
  fs.mkdirSync(FRAMES, { recursive: true });
}

async function preparePage(page, theme = "light") {
  await page.addInitScript((mode) => {
    localStorage.setItem("lt-theme", mode);
    localStorage.setItem("ltcai_workspace_type", "personal");
    localStorage.setItem("ltcai_mode", "advanced");
    localStorage.setItem("ltcai_user_email", "demo@lattice.local");
    localStorage.setItem("ltcai_is_admin", "true");
    sessionStorage.setItem("ltcai_force_setup_after_login", "false");
  }, theme);
}

async function gotoReady(page, pathname, selector, theme = "light") {
  await preparePage(page, theme);
  await page.goto(new URL(pathname, BASE).toString(), { waitUntil: "domcontentloaded", timeout: 30000 });
  if (selector) await page.waitForSelector(selector, { timeout: 20000 });
  await page.evaluate((mode) => {
    document.documentElement.setAttribute("data-lt-theme", mode);
    document.body.dataset.capture = "release-221";
  }, theme);
  await page.waitForTimeout(1800);
}

async function screenshot(page, filename, options = {}) {
  const out = path.join(OUT, filename);
  await page.screenshot({ path: out, fullPage: Boolean(options.fullPage) });
  console.log(out);
}

async function captureAll() {
  ensureDirs();
  const { chromium } = await loadPlaywright();
  const browser = await chromium.launch({ headless: true });

  const desktop = await browser.newContext({
    viewport: { width: 1440, height: 920 },
    deviceScaleFactor: 1,
  });
  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
  });

  let page = await desktop.newPage();
  await gotoReady(page, "/workspace", "#workspace-health-grid", "light");
  await screenshot(page, "workspace-light.png");
  await screenshot(page, "lattice-ai-hero.png");

  await page.evaluate(() => document.documentElement.setAttribute("data-lt-theme", "dark"));
  await page.waitForTimeout(800);
  await screenshot(page, "workspace-dark.png");

  await gotoReady(page, "/graph", "#graph", "dark");
  await screenshot(page, "knowledge-graph.png");

  await gotoReady(page, "/workflows", "#wfNodes", "light");
  await screenshot(page, "pipeline.png");

  await gotoReady(page, "/admin", "#admin-root, .admin-shell, body", "light");
  await page.waitForTimeout(2200);
  await screenshot(page, "admin-dashboard.png");

  const mobilePage = await mobile.newPage();
  await gotoReady(mobilePage, "/chat", ".reference-shell, body", "light");
  await screenshot(mobilePage, "mobile-responsive.png", { fullPage: false });

  const framePlan = [
    ["/workspace", "#workspace-health-grid", "light"],
    ["/workspace", "#workspace-health-grid", "dark"],
    ["/graph", "#graph", "dark"],
    ["/workflows", "#wfNodes", "light"],
    ["/chat", ".reference-shell, body", "light"],
  ];
  let frame = 0;
  for (const [pathname, selector, theme] of framePlan) {
    await gotoReady(page, pathname, selector, theme);
    for (let repeat = 0; repeat < 3; repeat += 1) {
      await page.screenshot({ path: path.join(FRAMES, `frame_${String(frame).padStart(3, "0")}.png`) });
      frame += 1;
    }
  }

  await browser.close();

  const gifPath = path.join(OUT, "lattice-ai-demo.gif");
  const ffmpeg = spawnSync("ffmpeg", [
    "-y",
    "-framerate", "1.5",
    "-i", path.join(FRAMES, "frame_%03d.png"),
    "-vf", "scale=1280:-1:flags=lanczos,fps=6",
    "-loop", "0",
    gifPath,
  ], { stdio: "inherit" });
  if (ffmpeg.status !== 0) process.exit(ffmpeg.status || 1);
  console.log(gifPath);
}

captureAll().catch((error) => {
  console.error(error);
  process.exit(1);
});
