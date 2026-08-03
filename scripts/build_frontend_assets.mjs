#!/usr/bin/env node
// Post-processes the `vite build` output in place: normalises trailing
// whitespace in emitted css/js, rewrites the raw vite manifest into the
// app-facing asset-manifest.json, and stamps the service-worker cache version.
//
// `--out-dir <dir>` points the first two steps at a directory other than
// static/app so scripts/check_frontend_build_freshness.mjs can reproduce the
// exact shipped bytes into a scratch tree. In that mode the static/sw.js
// rewrite is skipped: it is a repo-level side effect, not part of the app dir,
// and a freshness check must never mutate the working tree.
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const argv = process.argv.slice(2);
const outDirFlag = argv.indexOf("--out-dir");
const outDirArg = outDirFlag === -1 ? null : argv[outDirFlag + 1];
if (outDirFlag !== -1 && !outDirArg) {
  console.error("--out-dir requires a directory argument");
  process.exit(2);
}

const repo = join(import.meta.dirname, "..");
const appDir = outDirArg ? resolve(outDirArg) : join(repo, "static", "app");
const nestedViteManifest = join(appDir, ".vite", "asset-manifest.json");
const publicManifest = join(appDir, "asset-manifest.json");
const serviceWorker = join(repo, "static", "sw.js");
const pkg = JSON.parse(readFileSync(join(repo, "package.json"), "utf8"));

const assetsDir = join(appDir, "assets");
if (existsSync(assetsDir)) {
  for (const name of readdirSync(assetsDir)) {
    if (!/\.(?:css|js)$/.test(name)) continue;
    const file = join(assetsDir, name);
    const text = readFileSync(file, "utf8");
    const normalized = text.replace(/[ \t]+$/gm, "");
    if (normalized !== text) writeFileSync(file, normalized, "utf8");
  }
}

const viteManifest = existsSync(nestedViteManifest) ? nestedViteManifest : publicManifest;
if (!existsSync(viteManifest)) {
  console.error("Vite manifest missing. Run `vite build` before build_frontend_assets.mjs.");
  process.exit(1);
}

const raw = JSON.parse(readFileSync(viteManifest, "utf8"));
const assets = {};
for (const [key, value] of Object.entries(raw)) {
  if (value && typeof value === "object") {
    if (value.file) assets[key] = `/static/app/${value.file}`;
    for (const css of value.css || []) assets[css] = `/static/app/${css}`;
    for (const asset of value.assets || []) assets[asset] = `/static/app/${asset}`;
  }
}

const manifest = {
  version: pkg.version,
  generated_at: "vite",
  entrypoints: {
    app: assets["index.html"] || "/static/app/index.html",
  },
  assets,
};

writeFileSync(publicManifest, JSON.stringify(manifest, null, 2) + "\n", "utf8");
if (!outDirArg && existsSync(serviceWorker)) {
  const cacheVersion = pkg.version.replace(/\D/g, "");
  const source = readFileSync(serviceWorker, "utf8");
  const normalized = source.replace(
    /const CACHE = "lattice-v[^"]+";/,
    `const CACHE = "lattice-v${cacheVersion}";`,
  );
  if (normalized !== source) writeFileSync(serviceWorker, normalized, "utf8");
}
console.log(`wrote ${publicManifest} with ${Object.keys(assets).length} assets`);
