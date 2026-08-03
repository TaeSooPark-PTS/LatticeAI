#!/usr/bin/env node
/**
 * Frontend build freshness gate.
 *
 * Why this exists
 * ---------------
 * The Playwright visual suite (`npm run test:visual`) drives the *committed*
 * build output in `static/app/`. Nothing else proves that directory was built
 * from the current `frontend/src/`. Change a component, forget
 * `npm run build:assets`, and CI goes green against a UI that no longer exists.
 *
 * What it does
 * ------------
 * Reproduces `npm run build:assets` into a scratch directory and compares the
 * result byte-for-byte with the committed `static/app/`. It runs the same two
 * steps as the real build, in the same order:
 *   1. `vite build --outDir <scratch> --emptyOutDir`
 *   2. `scripts/build_frontend_assets.mjs --out-dir <scratch>`
 * Step 2 matters: it strips trailing whitespace from the emitted css/js *after*
 * vite has already computed the content hash in the filename, so the shipped
 * bytes are not the raw rollup output. Comparing against raw vite output alone
 * produces a guaranteed false failure on at least one chunk.
 *
 * It additionally checks that `static/sw.js` carries the cache constant that
 * `build:assets` stamps from package.json, because that write is the one part
 * of the build that lands outside `static/app/`.
 *
 * Determinism (measured, not assumed)
 * -----------------------------------
 * On an unchanged tree two consecutive `vite build` runs are byte-identical,
 * including the content-hashed filenames, and the output does not depend on
 * where `--outDir` points (verified with outDirs inside and outside the repo).
 * So byte-exact comparison is the strictest gate that is actually stable, and
 * that is what this script does.
 *
 * What it does NOT catch
 * ----------------------
 *   * Toolchain drift. It compares a build made *now, here* against bytes
 *     committed *then, there*. A different vite/rolldown/tailwind version, or a
 *     platform whose native bindings emit different bytes, would fail this gate
 *     even though the source and the build output agree. `npm ci` against the
 *     committed package-lock.json is what keeps that from happening; if this
 *     gate ever fails with an identical file *set* but differing bytes, suspect
 *     the toolchain before suspecting the author.
 *   * Whether the built UI is *correct*. That is the visual suite's job. This
 *     only proves the visual suite is looking at the current source.
 *   * Anything outside `static/app/` and the `static/sw.js` cache constant.
 *
 * Exit codes: 0 fresh, 1 stale, 2 could not run the comparison at all.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const committedDir = join(repo, "static", "app");
const viteCli = join(repo, "node_modules", "vite", "bin", "vite.js");
const postBuild = join(repo, "scripts", "build_frontend_assets.mjs");

// vite writes its raw manifest to a `.vite/` subdirectory under some configs.
// build_frontend_assets.mjs reads it from either location and the repo does not
// commit it, so excluding it from both sides avoids a false "extra file".
const IGNORED_PREFIXES = [".vite/"];

function fail(message, code = 1) {
  console.error(message);
  process.exit(code);
}

function run(label, file, args) {
  const result = spawnSync(process.execPath, [file, ...args], {
    cwd: repo,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) fail(`${label} could not start: ${result.error.message}`, 2);
  if (result.status !== 0) fail(`${label} failed with status ${result.status ?? "unknown"}`, 2);
}

function walk(root, base = root, out = new Map()) {
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const full = join(root, entry.name);
    const rel = relative(base, full).split("\\").join("/");
    if (IGNORED_PREFIXES.some((prefix) => rel === prefix.slice(0, -1) || rel.startsWith(prefix))) {
      continue;
    }
    if (entry.isDirectory()) walk(full, base, out);
    else out.set(rel, full);
  }
  return out;
}

function checkServiceWorker() {
  const swPath = join(repo, "static", "sw.js");
  if (!existsSync(swPath)) return null;
  const version = JSON.parse(readFileSync(join(repo, "package.json"), "utf8")).version;
  const expected = `const CACHE = "lattice-v${version.replace(/\D/g, "")}";`;
  return readFileSync(swPath, "utf8").includes(expected)
    ? null
    : `static/sw.js is missing ${expected} (build:assets stamps it from package.json).`;
}

if (!existsSync(committedDir)) {
  fail(`Committed build output is missing: ${committedDir}\nRun \`npm run build:assets\` and commit static/app/.`);
}
if (!existsSync(viteCli)) {
  fail(`vite is not installed at ${viteCli}. Run \`npm ci\` first.`, 2);
}

const scratchRoot = mkdtempSync(join(tmpdir(), "ltcai-frontend-freshness-"));
const freshDir = join(scratchRoot, "app");
let exitCode = 0;

try {
  run("vite build", viteCli, ["build", "--outDir", freshDir, "--emptyOutDir"]);
  run("build_frontend_assets.mjs", postBuild, ["--out-dir", freshDir]);

  const fresh = walk(freshDir);
  const committed = walk(committedDir);

  const missing = [...committed.keys()].filter((rel) => !fresh.has(rel)).sort();
  const extra = [...fresh.keys()].filter((rel) => !committed.has(rel)).sort();
  const changed = [...fresh.keys()]
    .filter((rel) => committed.has(rel))
    .filter((rel) => !readFileSync(fresh.get(rel)).equals(readFileSync(committed.get(rel))))
    .sort();

  const swProblem = checkServiceWorker();

  if (!missing.length && !extra.length && !changed.length && !swProblem) {
    console.log(
      `static/app/ is in sync with frontend/src/ (${committed.size} files rebuilt and byte-identical).`,
    );
  } else {
    exitCode = 1;
    console.error("\nstatic/app/ is STALE — it was not built from the current frontend/src/.\n");
    if (extra.length) {
      console.error(`  ${extra.length} file(s) the current source produces but static/app/ does not have:`);
      for (const rel of extra.slice(0, 20)) console.error(`    + ${rel}`);
      if (extra.length > 20) console.error(`    ... and ${extra.length - 20} more`);
    }
    if (missing.length) {
      console.error(`  ${missing.length} committed file(s) the current source no longer produces:`);
      for (const rel of missing.slice(0, 20)) console.error(`    - ${rel}`);
      if (missing.length > 20) console.error(`    ... and ${missing.length - 20} more`);
    }
    if (changed.length) {
      console.error(`  ${changed.length} file(s) with the same name but different bytes:`);
      for (const rel of changed.slice(0, 20)) {
        const a = statSync(committed.get(rel)).size;
        const b = statSync(fresh.get(rel)).size;
        console.error(`    ~ ${rel} (committed ${a} bytes, rebuilt ${b} bytes)`);
      }
      if (changed.length > 20) console.error(`    ... and ${changed.length - 20} more`);
      console.error(
        "    Same names with different bytes and no added/removed files can also mean\n" +
          "    toolchain drift rather than stale output — check that `npm ci` installed the\n" +
          "    versions in package-lock.json before assuming the source changed.",
      );
    }
    if (swProblem) console.error(`  ${swProblem}`);
    console.error("\nFix: run `npm run build:assets` and commit static/app/ (and static/sw.js).\n");
  }
} finally {
  rmSync(scratchRoot, { recursive: true, force: true });
}

process.exit(exitCode);
