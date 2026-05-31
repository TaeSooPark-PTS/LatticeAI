const fs = require("fs");
const path = require("path");

async function loadPlaywright() {
  try {
    return require("@playwright/test");
  } catch (_) {
    return require("playwright");
  }
}

async function capturePage(options) {
  const { chromium } = await loadPlaywright();
  const baseURL = process.env.LTCAI_CAPTURE_BASE_URL || "http://localhost:4825";
  const outDir = path.resolve(process.env.LTCAI_CAPTURE_OUT || path.join(__dirname, "..", "..", "docs", "images"));
  const sessionToken = process.env.SESSION_TOKEN || process.env.LTCAI_SESSION_TOKEN || "";
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({ headless: process.env.LTCAI_CAPTURE_HEADED !== "1" });
  const context = await browser.newContext({
    viewport: { width: Number(process.env.LTCAI_CAPTURE_WIDTH || 1440), height: Number(process.env.LTCAI_CAPTURE_HEIGHT || 920) },
    deviceScaleFactor: Number(process.env.LTCAI_CAPTURE_SCALE || 2),
  });
  if (sessionToken) {
    const host = new URL(baseURL).hostname;
    await context.addCookies([{ name: "session_token", value: sessionToken, domain: host, path: "/", httpOnly: true, secure: false }]);
  }

  const page = await context.newPage();
  const target = new URL(options.path, baseURL).toString();
  await page.goto(target, { waitUntil: "networkidle", timeout: Number(process.env.LTCAI_CAPTURE_TIMEOUT || 30_000) });
  if (options.hash) await page.evaluate((hash) => { location.hash = hash; }, options.hash);
  if (options.waitFor) await page.waitForSelector(options.waitFor, { timeout: 15_000 });
  await page.waitForTimeout(Number(process.env.LTCAI_CAPTURE_SETTLE_MS || options.settleMs || 900));

  const outPath = path.join(outDir, options.filename);
  await page.screenshot({ path: outPath, fullPage: Boolean(options.fullPage) });
  await browser.close();
  console.log(outPath);
  return outPath;
}

module.exports = { capturePage };
