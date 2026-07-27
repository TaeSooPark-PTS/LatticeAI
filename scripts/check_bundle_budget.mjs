#!/usr/bin/env node
// Guards the initial JavaScript payload of the /app SPA so heavy features stay
// behind lazy boundaries. It measures the entry chunk plus its transitive
// STATIC import closure (what a browser must download before first paint),
// gzips each file, and fails if the total exceeds the budget.
//
// The dynamic import() chunks (pages, onboarding, conversation home, command
// palette) are intentionally excluded — they are fetched on demand.
//
// The check builds into an isolated temp dir so it reads Vite's rich manifest
// (with `isEntry`/`imports`) without clobbering the production `static/app`
// manifest, which `build_frontend_assets.mjs` rewrites into a flat runtime map.
import { existsSync, readFileSync, rmSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const repo = join(import.meta.dirname, "..");
const outDir = join(repo, "node_modules", ".cache", "bundle-budget");
const manifestPath = join(outDir, "asset-manifest.json");

// Budget for the initial static JS closure, in gzipped bytes. Set below the
// pre-split baseline (632.9 kB raw / ~180 kB gzip single chunk) to lock in the
// code-splitting win and catch regressions that pull heavy code back onto the
// first-paint path. Raise it only with a deliberate, reviewed reason.
//
// 9.9.8: 150 KiB → 152 KiB. The 9.9.7 tree sat at 149.3 KiB, i.e. 0.7 KiB of
// headroom, and the autonomy-dial settings panel needed ~1.0 KiB — almost all
// of it bilingual UI copy. Page components are already lazy (the panel itself
// lands in the System chunk), but `frontend/src/i18n/*` is merged into one
// synchronous table that `t()` reads, so *every* surface's copy is on the
// first-paint path by construction. That is the real constraint here, not code
// weight, and the headroom was accidental rather than chosen. Splitting i18n
// per lazy route is the durable fix and is deliberately NOT bundled into this
// release. Until then this ceiling still catches what it was built to catch:
// a heavy module escaping its lazy boundary moves this by tens of KiB, not one.
const INITIAL_JS_GZIP_BUDGET = 155_648; // 152 KiB

function build() {
  rmSync(outDir, { recursive: true, force: true });
  const result = spawnSync(
    "npx",
    ["vite", "build", "--outDir", outDir, "--emptyOutDir"],
    { cwd: repo, encoding: "utf8", stdio: ["ignore", "ignore", "inherit"] },
  );
  if (result.status !== 0) {
    console.error("bundle budget: vite build failed");
    process.exit(1);
  }
  if (!existsSync(manifestPath)) {
    console.error("bundle budget: manifest missing after build");
    process.exit(1);
  }
}

function measure() {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry);
  if (!entryKey) {
    console.error("bundle budget: no entry chunk in manifest");
    process.exit(1);
  }

  const seen = new Set();
  const walk = (key) => {
    if (seen.has(key) || !manifest[key]) return;
    seen.add(key);
    for (const imp of manifest[key].imports || []) walk(imp);
  };
  walk(entryKey);

  const rows = [];
  let rawTotal = 0;
  let gzipTotal = 0;
  for (const key of seen) {
    const chunk = manifest[key];
    if (!chunk?.file || !chunk.file.endsWith(".js")) continue;
    const buffer = readFileSync(join(outDir, chunk.file));
    const gzip = gzipSync(buffer).length;
    rawTotal += buffer.length;
    gzipTotal += gzip;
    rows.push({ file: chunk.file, raw: buffer.length, gzip });
  }
  rows.sort((a, b) => b.gzip - a.gzip);

  const lazyChunks = Object.values(manifest)
    .filter((chunk) => chunk?.file?.endsWith(".js") && !seen.has(chunk.name ?? ""))
    .filter((chunk) => !rows.some((row) => row.file === chunk.file));

  return { rows, rawTotal, gzipTotal, lazyCount: lazyChunks.length };
}

const kib = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`;

build();
const { rows, rawTotal, gzipTotal, lazyCount } = measure();
rmSync(outDir, { recursive: true, force: true });

console.log("Initial static JS (entry + static imports):");
for (const row of rows) {
  console.log(`  ${row.file.padEnd(40)} raw ${kib(row.raw).padStart(11)}  gzip ${kib(row.gzip).padStart(11)}`);
}
console.log(`  ${"TOTAL".padEnd(40)} raw ${kib(rawTotal).padStart(11)}  gzip ${kib(gzipTotal).padStart(11)}`);
console.log(`Budget (initial JS gzip): ${kib(INITIAL_JS_GZIP_BUDGET)}`);
console.log(`Lazy JS chunks kept off first paint: ${lazyCount}`);

if (gzipTotal > INITIAL_JS_GZIP_BUDGET) {
  console.error(
    `\nbundle budget: FAIL — initial JS ${kib(gzipTotal)} gzip exceeds budget ${kib(INITIAL_JS_GZIP_BUDGET)}.\n`
    + "Move heavy modules behind React.lazy/dynamic import() or justify a budget bump.",
  );
  process.exit(1);
}
console.log(`\nbundle budget: PASS — initial JS ${kib(gzipTotal)} gzip within ${kib(INITIAL_JS_GZIP_BUDGET)}.`);
