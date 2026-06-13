#!/usr/bin/env node
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const repo = join(import.meta.dirname, "..");
const appDir = join(repo, "static", "app");
const nestedViteManifest = join(appDir, ".vite", "asset-manifest.json");
const publicManifest = join(appDir, "asset-manifest.json");
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
    app: assets["frontend/index.html"] || "/static/app/index.html",
  },
  assets,
  vite: raw,
};

writeFileSync(publicManifest, JSON.stringify(manifest, null, 2) + "\n", "utf8");
console.log(`wrote static/app/asset-manifest.json with ${Object.keys(assets).length} assets`);
