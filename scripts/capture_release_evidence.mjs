import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const repoRoot = process.cwd();
const version = JSON.parse(fs.readFileSync(path.join(repoRoot, "package.json"), "utf8")).version;
const port = Number(process.env.LTCAI_RELEASE_EVIDENCE_PORT || 4936);
const baseURL = `http://127.0.0.1:${port}`;
// Defaults to the published directory for this version, but can be redirected.
// This script starts by deleting `root` outright, so anything that wants a
// preview capture — a reviewer checking whether the UI actually moved before
// the version is bumped — would otherwise have to overwrite the screenshots of
// an already-released version to get one.
const root =
  process.env.LTCAI_RELEASE_EVIDENCE_DIR ||
  path.join(repoRoot, "output", "release", `v${version}`);
const screenshots = path.join(root, "screenshots");
const videos = path.join(root, "videos");
const gifs = path.join(root, "gifs");
const manifestPath = path.join(repoRoot, "static", "app", "asset-manifest.json");

function assetManifestFingerprint() {
  if (!fs.existsSync(manifestPath)) {
    return null;
  }
  const body = fs.readFileSync(manifestPath);
  const stat = fs.statSync(manifestPath);
  return {
    sha256: createHash("sha256").update(body).digest("hex"),
    mtime: stat.mtime.toISOString(),
    bytes: body.length,
  };
}

/**
 * Refuse to capture when the release lint gate is red.
 *
 * Failure mode this catches: lint is failing (e.g. ruff F841) but
 * capture still runs and writes 12 screenshots + SCREENSHOT_INDEX.md
 * bound to the asset manifest — evidence that looks green while the
 * first CI stage would already have stopped. Capture is not proof that
 * the tree is releasable; lint must pass first.
 *
 * Guard runs *before* wiping `root`, so a red lint never deletes the
 * last good evidence directory.
 */
function assertLintClean() {
  console.log("release evidence: running npm run lint before capture…");
  try {
    // Skip the evidence↔build binding gate during intentional recapture:
    // this run is about to wipe and rebind SCREENSHOT_INDEX.md. Day-to-day
    // `npm run lint` still hard-fails when screenshots are stale.
    execFileSync("npm", ["run", "lint"], {
      cwd: repoRoot,
      stdio: "inherit",
      env: { ...process.env, LTCAI_SKIP_RELEASE_EVIDENCE_BOUND: "1" },
    });
  } catch (err) {
    const code = err && typeof err === "object" && "status" in err ? err.status : 1;
    console.error(
      "release evidence: npm run lint failed — capture aborted.\n" +
        "  Fix lint (ruff / frontend / openapi / i18n gates) before release:evidence.\n" +
        `  exit status: ${code}`,
    );
    process.exit(typeof code === "number" && code !== 0 ? code : 1);
  }
}

/**
 * Refuse to capture when static/app is older than frontend/src.
 *
 * Failure mode this catches: someone edits frontend/src, skips
 * `npm run build:assets`, then runs release:evidence. Screenshots look
 * "fresh" (new mtimes) but still show the previous build. That shipped
 * stale evidence in 10.6.3 (asset mtime later than screenshot mtime by
 * 42–50s). Warn-and-continue is useless — exit 1 so CI and humans stop.
 *
 * Guard runs *before* wiping `root`, so a stale build never deletes the
 * last good evidence directory.
 */
function assertBuiltAssetsFresh() {
  const srcRoot = path.join(repoRoot, "frontend", "src");
  if (!fs.existsSync(manifestPath)) {
    console.error(
      "release evidence: static/app/asset-manifest.json 이 없습니다.\n" +
        "실행 전에 npm run build:assets 를 돌려라.",
    );
    process.exit(1);
  }
  if (!fs.existsSync(srcRoot)) {
    console.error(`release evidence: frontend/src 가 없습니다: ${srcRoot}`);
    process.exit(1);
  }

  let newestSrcMtime = 0;
  let newestSrcPath = srcRoot;
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.isFile()) continue;
      const mtime = fs.statSync(full).mtimeMs;
      if (mtime > newestSrcMtime) {
        newestSrcMtime = mtime;
        newestSrcPath = full;
      }
    }
  };
  walk(srcRoot);

  const manifestMtime = fs.statSync(manifestPath).mtimeMs;
  if (newestSrcMtime > manifestMtime) {
    const rel = path.relative(repoRoot, newestSrcPath);
    console.error(
      "release evidence: frontend/src 가 static/app 빌드보다 더 새롭습니다.\n" +
        `  최신 소스: ${rel}\n` +
        `  소스 mtime:  ${new Date(newestSrcMtime).toISOString()}\n` +
        `  빌드 mtime:  ${new Date(manifestMtime).toISOString()} (static/app/asset-manifest.json)\n` +
        "실행 전에 npm run build:assets 를 돌려라.",
    );
    process.exit(1);
  }
}

/**
 * Detect evidence that was captured against a *previous* asset build.
 *
 * Failure mode: capture runs (screenshots mtime T0), then someone rebuilds
 * assets (manifest mtime T1 > T0). The pre-guard only compares src vs
 * manifest, so it still passes — but screenshots no longer show the build
 * that will ship. Recording the manifest sha256 in SCREENSHOT_INDEX.md at
 * capture time, then refusing to trust a mismatched index, closes that hole.
 *
 * Day-to-day gate: `scripts/check_release_evidence_bound.mjs` (in npm run
 * lint) hard-fails on sha mismatch. This pre-capture check is different —
 * when someone deliberately runs release:evidence after build:assets, the
 * existing index is *expected* to be stale; we continue, wipe, and rebind.
 * Set LTCAI_RELEASE_EVIDENCE_REQUIRE_BOUND=1 to refuse wipe-and-rebind
 * (useful in CI that should never recapture from a dirty tree).
 */
function assertExistingEvidenceStillBoundToBuild() {
  const indexPath = path.join(root, "SCREENSHOT_INDEX.md");
  if (!fs.existsSync(indexPath)) return;
  const index = fs.readFileSync(indexPath, "utf8");
  const match = index.match(/asset-manifest\.sha256:\s*`?([0-9a-f]{64})`?/i);
  if (!match) {
    // Older captures predate binding — this run will rebind after wipe.
    console.warn(
      "release evidence: existing SCREENSHOT_INDEX.md has no asset-manifest.sha256 binding.\n" +
        "  Recapture will record the current build fingerprint.",
    );
    return;
  }
  const recorded = match[1].toLowerCase();
  const current = assetManifestFingerprint();
  if (!current) return;
  if (recorded === current.sha256) return;

  const msg =
    "release evidence: SCREENSHOT_INDEX.md is bound to a different asset build.\n" +
    `  recorded sha256: ${recorded}\n` +
    `  current  sha256: ${current.sha256}\n` +
    `  current  mtime:  ${current.mtime}\n` +
    "Evidence screenshots are stale relative to static/app.";
  if (process.env.LTCAI_RELEASE_EVIDENCE_REQUIRE_BOUND === "1") {
    console.error(
      `${msg}\n  LTCAI_RELEASE_EVIDENCE_REQUIRE_BOUND=1 — refusing wipe/rebind.\n` +
        "  Unset the flag and re-run release:evidence to recapture, or restore the matching build.",
    );
    process.exit(1);
  }
  console.warn(
    `${msg}\n  Continuing: this run will wipe and rebind evidence to the current build.\n` +
      "  (Day-to-day lint still fails on stale evidence via check_release_evidence_bound.mjs.)",
  );
}

assertLintClean();
assertBuiltAssetsFresh();
assertExistingEvidenceStillBoundToBuild();

// A release capture must be reproducible and must never retain partial videos
// from an interrupted prior run.
fs.rmSync(root, { recursive: true, force: true });
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

  // These frames are the ones the README publishes, so they have to show the
  // product a new person actually lands in. The app's own default is `basic`
  // (frontend/src/store/appStore.ts) — capturing in `advanced` meant every
  // published screenshot showed raw payload panels, storage engines and hook
  // logs that no first-run user is ever shown. The admin console is captured
  // by route below and does not depend on this setting.
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("lattice.releaseEvidence.cleared")) {
      localStorage.removeItem("lattice.productFlow.complete");
      localStorage.removeItem("lattice.productFlow.user");
      localStorage.setItem("lattice.language", "ko");
      localStorage.setItem("lattice.mode", "basic");
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

  await page.goto(`${baseURL}/app#/knowledge-graph`, { waitUntil: "networkidle" });
  await page.locator("[data-testid='brain-cytoscape']").waitFor();
  const graphSearch = page.locator(
    "input[aria-label='Search knowledge graph'], input[placeholder*='Search'], input[placeholder*='검색']"
  ).first();
  if (await graphSearch.count()) {
    await graphSearch.fill("workspace");
  }
  await shot(page, "05-memory-graph.png");

  await page.goto(`${baseURL}/app#/capture`, { waitUntil: "networkidle" });
  await page.locator("h1.page-title", { hasText: "어떤 자료를 기억할까요?" }).waitFor();
  await shot(page, "06-capture.png");

  await page.goto(`${baseURL}/app#/models`, { waitUntil: "networkidle" });
  await page.locator("h1.page-title", { hasText: "Lattice가 사용할 AI를 선택하세요." }).waitFor();
  await shot(page, "07-model-library.png");

  await page.goto(`${baseURL}/app#/settings`, { waitUntil: "networkidle" });
  await page.locator("h1.page-title", { hasText: "Lattice를 내 방식에 맞게 설정하세요." }).waitFor();
  await shot(page, "08-system.png");

  // Captured in `basic`, the Brain page's knowledge-flow strip stays collapsed,
  // so this slot published a second copy of 04. The runs list carries what
  // actually changed instead: runs named the way their author named them, and
  // statuses spoken aloud rather than shown as `awaiting_approval`.
  await page.goto(`${baseURL}/app#/runs`, { waitUntil: "networkidle" });
  await page.locator("h1.page-title").waitFor();
  await page.getByText("내 승인 기다리는 중").first().waitFor();
  await shot(page, "09-automation-runs.png");

  await page.goto(`${baseURL}/app#/admin/users`, { waitUntil: "networkidle" });
  await page.waitForTimeout(250);
  await shot(page, "10-admin-console.png");

  // The three named steps a file walks on its way into memory. 10.6.0 moved
  // this out of a tab of its own and into Capture's second row, where it is now
  // always on screen — so `#/pipeline` renders the same page as `#/capture`,
  // and a full-page shot here came out byte-identical to 06. The README prints
  // the two as different tiles, so frame the card this tile is actually about.
  await page.goto(`${baseURL}/app#/pipeline`, { waitUntil: "networkidle" });
  const journeyList = page.getByRole("list", { name: "자료가 기억이 되는 3단계" });
  await journeyList.waitFor();
  const journeyCard = page
    .locator(".capture-secondary-column > *")
    .filter({ has: journeyList })
    .first();
  await page.waitForTimeout(120);
  await journeyCard.screenshot({ path: path.join(screenshots, "11-knowledge-journey.png") });

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
  // ffmpeg's default GIF palette is a fixed 256-colour cube, which turned the
  // app's ivory background into a dithered yellow and its greens into olive —
  // this GIF is the first thing on the README, so it was publishing a product
  // that does not exist. Generating the palette from the clip itself keeps the
  // real colours.
  execFileSync("ffmpeg", [
    "-y",
    "-i", webmTarget,
    "-filter_complex",
    "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];" +
      "[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=sierra2_4a",
    "-loop", "0",
    gifTarget,
  ], { stdio: "ignore" });

  const fingerprint = assetManifestFingerprint();
  if (!fingerprint) {
    throw new Error("asset-manifest.json missing after capture — cannot bind evidence");
  }
  // Post-capture binding: if a later build:assets changes the manifest hash,
  // check_release_evidence_bound.mjs / assertExistingEvidenceStillBoundToBuild
  // will refuse to trust these screenshots without a recapture.
  const index = `# v${version} Release Evidence

Captured from the built React/Vite app served by the release visual API on ${new Date().toISOString()}.

## Build Binding

Evidence is only trustworthy while this fingerprint matches
\`static/app/asset-manifest.json\`. A later \`build:assets\` without recapture
invalidates the screenshots even when their mtimes look fresh.

- asset-manifest.sha256: \`${fingerprint.sha256}\`
- asset-manifest.mtime: \`${fingerprint.mtime}\`
- asset-manifest.bytes: ${fingerprint.bytes}

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
| [09-automation-runs.png](screenshots/09-automation-runs.png) | Automation runs, named and status-spoken |
| [10-admin-console.png](screenshots/10-admin-console.png) | Separate Admin Console |
| [11-knowledge-journey.png](screenshots/11-knowledge-journey.png) | Material-to-memory steps |
| [12-review-center.png](screenshots/12-review-center.png) | Automation Review Center |

## Motion Evidence

- [v${version}-living-brain-walkthrough.webm](videos/v${version}-living-brain-walkthrough.webm)
- [v${version}-living-brain-walkthrough.gif](gifs/v${version}-living-brain-walkthrough.gif)
`;
  fs.writeFileSync(path.join(root, "SCREENSHOT_INDEX.md"), index);

  // Post-hoc: re-hash the manifest *now* and refuse if it drifted mid-run
  // (e.g. a parallel build:assets while Playwright was shooting).
  const after = assetManifestFingerprint();
  if (!after || after.sha256 !== fingerprint.sha256) {
    console.error(
      "release evidence: asset-manifest.json changed during capture.\n" +
        `  bound:    ${fingerprint.sha256}\n` +
        `  current:  ${after ? after.sha256 : "(missing)"}\n` +
        "Rebuild was not stable — delete evidence and re-run after a single build:assets.",
    );
    process.exit(1);
  }
  console.log(
    `release evidence bound to asset-manifest sha256=${fingerprint.sha256.slice(0, 12)}… mtime=${fingerprint.mtime}`,
  );
}

try {
  await main();
} finally {
  server.kill();
}
