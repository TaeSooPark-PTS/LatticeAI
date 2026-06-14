import { chromium } from "playwright";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const repoRoot = process.cwd();
const version = "5.2.0";
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
      sessionStorage.setItem("lattice.releaseEvidence.cleared", "true");
    }
  });

  await page.goto(`${baseURL}/app`, { waitUntil: "networkidle" });
  await shot(page, "01-login.png");

  await page.getByRole("textbox", { name: "You", exact: true }).fill("Codex");
  await page.getByRole("textbox", { name: "you@local", exact: true }).fill("codex@local");
  await page.getByPlaceholder("로컬 Brain 비밀번호").fill("Lattice123");
  await page.getByRole("button", { name: "내 Brain 시작하기" }).click();
  await page.getByText("이 컴퓨터를 확인합니다.").waitFor();
  await shot(page, "02-environment-analysis.png");

  await page.getByRole("button", { name: "추천 모델 보기" }).click();
  await page.getByText("추천 모델로 시작하세요.").waitFor();
  await shot(page, "03-recommended-models.png");

  await page.getByRole("button", { name: "추천대로 시작하기" }).click();
  await page.getByText("모델을 설치하고 시작합니다.").waitFor();
  await page.getByRole("button", { name: "다운로드하고 시작하기" }).click();
  await page.waitForTimeout(180);
  await shot(page, "04-install-load-progress.png");
  await page.getByText("Brain이 준비되었습니다.").waitFor();
  await shot(page, "11-model-setup-status.png");

  await page.locator("main[aria-label='Lattice Brain']").waitFor();
  await shot(page, "05-brain-chat-home.png");
  await shot(page, "06-living-brain-level-1.png");

  await page.getByRole("button", { name: "기억 보기" }).click();
  await page.getByText("Memory Layer").first().waitFor();
  await shot(page, "07-memory-layer.png");

  await page.getByRole("button", { name: "주제 보기" }).click();
  await page.getByText("Knowledge Layer").first().waitFor();
  await shot(page, "08-knowledge-layer.png");

  await page.getByRole("button", { name: "관계 보기" }).click();
  await page.getByText("Relationship Layer").first().waitFor();
  await shot(page, "09-relationship-layer.png");

  await page.getByRole("button", { name: "그래프로 보기" }).click();
  await page.getByText("Knowledge Graph").first().waitFor();
  await page.getByLabel("Search knowledge graph").fill("workspace");
  await shot(page, "10-knowledge-graph-layer.png");

  await page.getByRole("button", { name: "관리자 콘솔" }).click();
  await page.locator("main[aria-label='Lattice Admin']").waitFor();
  await shot(page, "12-admin-console.png");

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
| [02-environment-analysis.png](screenshots/02-environment-analysis.png) | Environment Analysis |
| [03-recommended-models.png](screenshots/03-recommended-models.png) | Recommended Models |
| [04-install-load-progress.png](screenshots/04-install-load-progress.png) | Install & Load progress |
| [05-brain-chat-home.png](screenshots/05-brain-chat-home.png) | Brain Chat home |
| [06-living-brain-level-1.png](screenshots/06-living-brain-level-1.png) | Living Brain Level 1 |
| [07-memory-layer.png](screenshots/07-memory-layer.png) | Memory Layer |
| [08-knowledge-layer.png](screenshots/08-knowledge-layer.png) | Knowledge Layer |
| [09-relationship-layer.png](screenshots/09-relationship-layer.png) | Relationship Layer |
| [10-knowledge-graph-layer.png](screenshots/10-knowledge-graph-layer.png) | Knowledge Graph layer with search |
| [11-model-setup-status.png](screenshots/11-model-setup-status.png) | Model setup status |
| [12-admin-console.png](screenshots/12-admin-console.png) | Separate Admin Console |

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
