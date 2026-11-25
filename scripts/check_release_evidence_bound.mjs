#!/usr/bin/env node
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  MOCK_SERVER_ENTRY,
  MOCK_SERVER_LABEL,
  mockServerFingerprint,
} from "./lib/mock_server_fingerprint.mjs";

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
const mockFp = mockServerFingerprint(repoRoot);
if (!mockFp) {
  fail(`release evidence binding: ${MOCK_SERVER_ENTRY} missing`, 2);
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
      `  Capture data comes from ${MOCK_SERVER_LABEL}; re-run\n` +
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
const currentMock = mockFp.sha256;

if (recordedMock !== currentMock) {
  fail(
    "release evidence binding: the mock server changed since capture.\n" +
      `  recorded mock-server.sha256: ${recordedMock}\n` +
      `  current  mock-server.sha256: ${currentMock}\n` +
      `  covers:  ${MOCK_SERVER_LABEL} (${mockFp.files} file(s), ${mockFp.bytes} bytes)\n` +
      `  newest   mtime:  ${mockFp.mtime}\n` +
      "  Mock API payloads changed after capture, so the screenshots may show\n" +
      "  responses the tree no longer produces. Re-capture the evidence:\n" +
      "    npm run release:evidence\n" +
      "  (bump version first if vX.Y.Z is already tagged — see docs/OPERATIONS.md).\n" +
      "  v11.3.0 widened this fingerprint from the entry file alone to the entry\n" +
      "  plus its route modules, so an evidence set captured before v11.3.0\n" +
      "  reports a mismatch until it is re-captured.",
  );
}

console.log(
  `release evidence binding ok: asset=${current.slice(0, 12)}… mock=${currentMock.slice(0, 12)}… (${mockFp.files} mock file(s)) mtime=${mtime} dir=${path.relative(repoRoot, root)}`,
);
