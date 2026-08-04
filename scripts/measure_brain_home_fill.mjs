#!/usr/bin/env node
/**
 * Measure Brain chat home (screen 04) content fill against the viewport.
 *
 * The layout rebuild requires the empty home's content bottom to reach at
 * least 90% of the 920px capture viewport — not "looks better" prose.
 *
 * Walks the same onboarding path as capture_release_evidence.mjs so #/brain
 * is the empty first-run home against the visual mock server.
 *
 * Exit 0 when bottom/viewport >= --min-ratio (default 0.90), else 1.
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";

const repoRoot = process.cwd();
const port = Number(process.env.LTCAI_RELEASE_EVIDENCE_PORT || 4937);
const baseURL = `http://127.0.0.1:${port}`;
const minRatio = Number(process.env.LTCAI_HOME_FILL_MIN || "0.90");

const server = spawn(process.execPath, ["tests/visual/mock_server.cjs"], {
  cwd: repoRoot,
  env: { ...process.env, LTCAI_VISUAL_PORT: String(port) },
  stdio: ["ignore", "pipe", "pipe"],
});

async function waitForServer() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseURL}/health`);
      if (res.ok) return;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`visual server did not start on ${baseURL}`);
}

async function main() {
  await waitForServer();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 920 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);

  // Same first-run reset as release capture — without this the product flow
  // may already be complete and the wake button never appears.
  await page.addInitScript(() => {
    localStorage.removeItem("lattice.productFlow.complete");
    localStorage.removeItem("lattice.productFlow.user");
    localStorage.setItem("lattice.language", "ko");
    localStorage.setItem("lattice.mode", "basic");
  });

  await page.goto(`${baseURL}/app`, { waitUntil: "networkidle" });
  const korean = page.getByRole("button", { name: "한국어" });
  if (await korean.count()) {
    await korean.click();
  }
  await page.getByRole("button", { name: "Brain 지금 깨우기" }).click();
  await page.getByText("이 Brain의 주인을 정합니다.").waitFor();
  await page.getByPlaceholder(/You|나/).fill("Codex");
  await page.getByPlaceholder("you@local").fill("codex@local");
  await page.getByPlaceholder("로컬 Brain 비밀번호").fill("Lattice123");
  await page.getByRole("button", { name: "내 Brain 시작하기" }).click();
  await page.getByText("추천대로 시작하세요.").waitFor();
  await page.getByRole("button", { name: "추천으로 바로 시작" }).click();
  await page.getByText("모델을 준비하고 시작합니다.").waitFor();
  await page.getByRole("button", { name: "준비하고 시작하기" }).click();
  await page.waitForTimeout(180);
  await page
    .getByRole("button", { name: /시작|들어가|Brain 열기|Open Brain/i })
    .first()
    .click({ timeout: 5000 })
    .catch(() => {});
  await page.locator("main[aria-label='Lattice Brain']").waitFor({ timeout: 15000 });
  await page.waitForTimeout(250);

  const metrics = await page.evaluate(() => {
    const main = document.querySelector("main[aria-label='Lattice Brain']");
    const home =
      document.querySelector(".brain-centered-home") ||
      document.querySelector(".brain-home-station") ||
      main;
    const quiet = document.querySelector(
      "footer.brain-home-quiet, .brain-home-quiet, .brain-home-shelves",
    );
    const composer = document.querySelector(
      ".brain-composer, [data-testid='brain-composer'], textarea",
    );
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, height: r.height, width: r.width };
    };
    const candidates = [home, quiet, composer, main].filter(Boolean);
    let bottom = 0;
    let top = Infinity;
    for (const el of candidates) {
      const r = el.getBoundingClientRect();
      bottom = Math.max(bottom, r.bottom);
      top = Math.min(top, r.top);
    }
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      main: box(main),
      home: box(home),
      quiet: box(quiet),
      composer: box(composer),
      contentTop: top === Infinity ? 0 : top,
      contentBottom: bottom,
    };
  });

  await browser.close();

  const ratio = metrics.contentBottom / metrics.viewport.height;
  const pct = (100 * ratio).toFixed(1);
  console.log(JSON.stringify({ ...metrics, fillRatio: ratio, fillPct: `${pct}%`, minRatio }, null, 2));
  console.log(
    `brain home fill: contentBottom=${metrics.contentBottom.toFixed(1)}px / viewport=${metrics.viewport.height}px = ${pct}% (need >= ${(100 * minRatio).toFixed(0)}%)`,
  );

  if (ratio + 1e-6 < minRatio) {
    console.error(
      `brain home fill FAIL: content only reaches ${pct}% of viewport (required ${(100 * minRatio).toFixed(0)}%).`,
    );
    process.exit(1);
  }
  console.log("brain home fill PASS");
}

try {
  await main();
} finally {
  server.kill();
}
