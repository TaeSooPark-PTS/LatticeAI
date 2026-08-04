#!/usr/bin/env node
/**
 * Post-capture evidence ↔ build binding gate.
 *
 * Failure mode
 * ------------
 * 1. `npm run release:evidence` writes screenshots (mtime T0).
 * 2. Someone runs `npm run build:assets` again (manifest mtime T1 > T0).
 * 3. The pre-capture guard only compares frontend/src mtime vs manifest mtime,
 *    so it still passes — while screenshots show an older UI than the build.
 *
 * Also: capture hits tests/visual/mock_server.cjs for API payloads. Changing
 * the mock after capture (e.g. taller Act panel → 1846px vs committed 1721px)
 * left asset-manifest.sha256 alone, so this gate used to stay green while
 * SCREENSHOT_INDEX.md described a UI the tree no longer produces.
 *
 * Capture records asset-manifest.sha256 and mock-server.sha256 in
 * SCREENSHOT_INDEX.md. This script fails (exit 1) when either binding is
 * missing or does not match the live file. Wired into `npm run lint` so a
 * green lint cannot ship stale screenshots.
 *
 * Exit 0 bound, 1 stale/missing, 2 could not run.
 *
 * Escape hatch: LTCAI_SKIP_RELEASE_EVIDENCE_BOUND=1 (used only by
 * release:evidence while it intentionally wipes and rebinds evidence).
 */
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const repoRoot = process.cwd();

if (process.env.LTCAI_SKIP_RELEASE_EVIDENCE_BOUND === "1") {
  console.log(
    "release evidence binding: skipped (LTCAI_SKIP_RELEASE_EVIDENCE_BOUND=1)",
  );
  process.exit(0);
}

const version = JSON.parse(fs.readFileSync(path.join(repoRoot, "package.json"), "utf8")).version;
const root =
  process.env.LTCAI_RELEASE_EVIDENCE_DIR ||
  path.join(repoRoot, "output", "release", `v${version}`);
const indexPath = path.join(root, "SCREENSHOT_INDEX.md");
const manifestPath = path.join(repoRoot, "static", "app", "asset-manifest.json");
const mockServerPath = path.join(repoRoot, "tests", "visual", "mock_server.cjs");

function fail(message, code = 1) {
  console.error(message);
  process.exit(code);
}

function sha256File(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

if (!fs.existsSync(indexPath)) {
  fail(`release evidence binding: missing ${path.relative(repoRoot, indexPath)}`);
}
if (!fs.existsSync(manifestPath)) {
  fail("release evidence binding: static/app/asset-manifest.json missing", 2);
}
if (!fs.existsSync(mockServerPath)) {
  fail("release evidence binding: tests/visual/mock_server.cjs missing", 2);
}

const index = fs.readFileSync(indexPath, "utf8");
const match = index.match(/asset-manifest\.sha256:\s*`?([0-9a-f]{64})`?/i);
if (!match) {
  fail(
    "release evidence binding: SCREENSHOT_INDEX.md has no asset-manifest.sha256.\n" +
      "  Re-run npm run release:evidence so the index records the build fingerprint.",
  );
}

const mockMatch = index.match(/mock-server\.sha256:\s*`?([0-9a-f]{64})`?/i);
if (!mockMatch) {
  fail(
    "release evidence binding: SCREENSHOT_INDEX.md has no mock-server.sha256.\n" +
      "  Capture data comes from tests/visual/mock_server.cjs; re-run\n" +
      "  npm run release:evidence so the index records the mock fingerprint.",
  );
}

const recorded = match[1].toLowerCase();
const current = sha256File(manifestPath);
const mtime = fs.statSync(manifestPath).mtime.toISOString();

if (recorded !== current) {
  fail(
    "release evidence binding: screenshots are stale relative to static/app.\n" +
      `  recorded sha256: ${recorded}\n` +
      `  current  sha256: ${current}\n` +
      `  current  mtime:  ${mtime}\n` +
      "  Rebuild happened after capture. Run: npm run build:assets && npm run release:evidence",
  );
}

const recordedMock = mockMatch[1].toLowerCase();
const currentMock = sha256File(mockServerPath);
const mockMtime = fs.statSync(mockServerPath).mtime.toISOString();

if (recordedMock !== currentMock) {
  fail(
    "release evidence binding: screenshots are stale relative to mock_server.cjs.\n" +
      `  recorded mock-server.sha256: ${recordedMock}\n` +
      `  current  mock-server.sha256: ${currentMock}\n` +
      `  current  mtime:  ${mockMtime}\n` +
      "  Mock API payloads changed after capture. Run: npm run release:evidence\n" +
      "  (bump version first if vX.Y.Z is already tagged — see docs/OPERATIONS.md).",
  );
}

console.log(
  `release evidence binding ok: asset=${current.slice(0, 12)}… mock=${currentMock.slice(0, 12)}… mtime=${mtime} dir=${path.relative(repoRoot, root)}`,
);
