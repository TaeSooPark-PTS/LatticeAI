import { chromium } from "playwright";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const repoRoot = process.cwd();
const version = "8.7.0";
const port = Number(process.env.LTCAI_RELEASE_EVIDENCE_PORT || 4936);
const baseURL = `http://127.0.0.1:${port}`;
const root = path.join(repoRoot, "output", "release", `v${version}`);
const screenshots = path.join(root, "screenshots");
const videos = path.join(root, "videos");
const gifs = path.join(root, "gifs");

fs.mkdirSync(screenshots, { recursive: true });
fs.mkdirSync(videos, { recursive: true });
fs.mkdirSync(gifs, { recursive: true });

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
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`visual server did not start on ${baseURL}`);
}

async function shot(page, name) {
  await page.waitForTimeout(120);
  await page.screenshot({ path: path.join(screenshots, name), fullPage: true });
}

async function openShellMenu(page) {
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("navigation", { name: "화면 이동" }).waitFor();
}

async function navigateShell(page, name) {
  await openShellMenu(page);
  await page.getByRole("button", { name, exact: true }).click();
}

async function main() {
  await waitForServer();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 920 },
    recordVideo: { dir: videos, size: { width: 1280, height: 818 } },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);

  await page.addInitScript(() => {
    if (!sessionStorage.getItem("lattice.releaseEvidence.cleared")) {
      localStorage.removeItem("lattice.productFlow.complete");
      localStorage.removeItem("lattice.productFlow.user");
      localStorage.setItem("lattice.language", "ko");
      localStorage.setItem("lattice.mode", "advanced");
      sessionStorage.setItem("lattice.releaseEvidence.cleared", "true");
    }
  });

  await page.goto(`${baseURL}/app`, { waitUntil: "networkidle" });
  const korean = page.getByRole("button", { name: "한국어" });
  if (await korean.count()) {
    await korean.click();
  }
  await page.getByRole("button", { name: "Brain 지금 깨우기" }).click();
  await page.getByText("이 Brain의 주인을 정합니다.").waitFor();
  await shot(page, "01-login.png");

  await page.getByPlaceholder(/You|나/).fill("Codex");
  await page.getByPlaceholder("you@local").fill("codex@local");
  await page.getByPlaceholder("로컬 Brain 비밀번호").fill("Lattice123");
  await page.getByRole("button", { name: "내 Brain 시작하기" }).click();
  await page.getByText("추천대로 시작하세요.").waitFor();
  await shot(page, "02-recommended-models.png");

  await page.getByRole("button", { name: "추천으로 바로 시작" }).click();
  await page.getByText("모델을 준비하고 시작합니다.").waitFor();
  await page.getByRole("button", { name: "준비하고 시작하기" }).click();
  await page.waitForTimeout(180);
  await shot(page, "03-install-load-progress.png");
  await page.locator("main[aria-label='Lattice Brain']").waitFor();
  await shot(page, "04-brain-chat-home.png");

  await navigateShell(page, "기억 그래프");
  await page.locator("[data-testid='brain-cytoscape']").waitFor();
  const graphSearch = page.locator(
    "input[aria-label='Search knowledge graph'], input[placeholder*='Search'], input[placeholder*='검색']"
  ).first();
  if (await graphSearch.count()) {
    await graphSearch.fill("workspace");
  }
  await shot(page, "05-memory-graph.png");

  await navigateShell(page, "자료 추가");
  await page.getByText(/Add memory sources|기억|자료|파일/).first().waitFor();
  await shot(page, "06-capture.png");

  await navigateShell(page, "모델 선택");
  await page.getByText(/Model Library|모델|Local Models|Installed/).first().waitFor();
  await shot(page, "07-model-library.png");

  await navigateShell(page, "관리");
  await page.getByText(/System|Settings|신원|공간|백업|설정/).first().waitFor();
  await shot(page, "08-system.png");

  await page.goto(`${baseURL}/app#/brain`, { waitUntil: "networkidle" });
  await page.locator("main[aria-label='Lattice Brain']").waitFor();
  await page.getByText("지금 Brain이 기억을 만들 준비가 됐습니다").waitFor();
  await page.waitForTimeout(250);
  await shot(page, "09-model-setup-status.png");

  await openShellMenu(page);
  await page.getByRole("button", { name: "관리자 콘솔 열기" }).click();
  await page.waitForTimeout(250);
  await shot(page, "10-admin-console.png");

  await page.goto(`${baseURL}/app#/review`, { waitUntil: "networkidle" });
  await page.getByText(/Review Center|리뷰 센터|검토함/).first().waitFor();
  const rawError = page.getByText(/not valid JSON|Unexpected token/);
  if (await rawError.count()) {
    throw new Error("Review Center release evidence contains a raw API parse error");
  }
  await shot(page, "12-review-center.png");

  const video = page.video();
  await context.close();
  await browser.close();

  const recorded = await video.path();
  const webmTarget = path.join(videos, `v${version}-living-brain-walkthrough.webm`);
  const gifTarget = path.join(gifs, `v${version}-living-brain-walkthrough.gif`);
  if (fs.existsSync(webmTarget)) fs.unlinkSync(webmTarget);
  if (fs.existsSync(gifTarget)) fs.unlinkSync(gifTarget);
  fs.renameSync(recorded, webmTarget);
  execFileSync("ffmpeg", [
    "-y",
    "-i", webmTarget,
    "-vf", "fps=8,scale=960:-1:flags=lanczos",
    "-loop", "0",
    gifTarget,
  ], { stdio: "ignore" });

  const index = `# v${version} Release Evidence

Captured from the built React/Vite app served by the release visual API on ${new Date().toISOString()}.

## Screenshots

| File | Flow |
| --- | --- |
| [01-login.png](screenshots/01-login.png) | Login |
| [02-recommended-models.png](screenshots/02-recommended-models.png) | Recommended Models |
| [03-install-load-progress.png](screenshots/03-install-load-progress.png) | Install & Load progress |
| [04-brain-chat-home.png](screenshots/04-brain-chat-home.png) | Brain Chat home |
| [05-memory-graph.png](screenshots/05-memory-graph.png) | Memory Graph |
| [06-capture.png](screenshots/06-capture.png) | Add Sources |
| [07-model-library.png](screenshots/07-model-library.png) | Model Library |
| [08-system.png](screenshots/08-system.png) | System |
| [09-model-setup-status.png](screenshots/09-model-setup-status.png) | Model setup status |
| [10-admin-console.png](screenshots/10-admin-console.png) | Separate Admin Console |
| [12-review-center.png](screenshots/12-review-center.png) | Automation Review Center |

## Motion Evidence

- [v${version}-living-brain-walkthrough.webm](videos/v${version}-living-brain-walkthrough.webm)
- [v${version}-living-brain-walkthrough.gif](gifs/v${version}-living-brain-walkthrough.gif)
`;
  fs.writeFileSync(path.join(root, "SCREENSHOT_INDEX.md"), index);
}

try {
  await main();
} finally {
  server.kill();
}
