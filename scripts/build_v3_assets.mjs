#!/usr/bin/env node
/*
 * Build the v3 browser asset manifest.
 *
 * The source files stay importable in development. This script writes hashed
 * siblings next to each runtime asset, rewrites ES-module imports to those
 * hashed siblings, and emits static/v3/asset-manifest.json for /app.
 */
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticRoot = join(repoRoot, "static");
const manifestPath = join(staticRoot, "v3", "asset-manifest.json");

const cssSources = [
  "static/css/tokens.css",
  "static/v3/css/lattice.tokens.css",
  "static/v3/css/lattice.base.css",
  "static/v3/css/lattice.components.css",
  "static/v3/css/lattice.shell.css",
  "static/v3/css/lattice.views.css",
];

const moduleRoot = join(staticRoot, "v3", "js");
const entry = "static/v3/js/app.js";

function posix(p) {
  return p.replaceAll("\\", "/");
}

function sha(text) {
  return createHash("sha256").update(text).digest("hex").slice(0, 8);
}

function repoPath(abs) {
  return posix(relative(repoRoot, abs));
}

function publicUrl(repoRel) {
  return "/" + posix(repoRel);
}

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) out.push(...walk(p));
    else if (name.endsWith(".js")) out.push(p);
  }
  return out;
}

function removeGenerated(dir, ext) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) removeGenerated(p, ext);
    else if (new RegExp(`\\.[0-9a-f]{8}\\${ext}$`).test(name)) rmSync(p);
  }
}

removeGenerated(join(staticRoot, "css"), ".css");
removeGenerated(join(staticRoot, "v3", "css"), ".css");
removeGenerated(moduleRoot, ".js");

const modules = new Map();
for (const abs of walk(moduleRoot)) {
  modules.set(repoPath(abs), {
    abs,
    rel: repoPath(abs),
    source: readFileSync(abs, "utf8"),
    deps: [],
  });
}

const importFromRe = /\b(?:import|export)\s+(?:[^"'()]*?\s+from\s*)?["']([^"']+\.js)["']/g;
for (const mod of modules.values()) {
  const deps = [];
  let match;
  while ((match = importFromRe.exec(mod.source))) {
    const spec = match[1];
    if (!spec.startsWith(".")) continue;
    const depRel = repoPath(join(dirname(mod.abs), spec));
    if (modules.has(depRel)) deps.push(depRel);
  }
  mod.deps = deps;
}

const hashMemo = new Map();
function moduleHash(rel, stack = []) {
  if (hashMemo.has(rel)) return hashMemo.get(rel);
  if (stack.includes(rel)) return sha(modules.get(rel).source);
  const mod = modules.get(rel);
  const depHashes = mod.deps
    .sort()
    .map((dep) => `${dep}:${moduleHash(dep, [...stack, rel])}`)
    .join("\n");
  const h = sha(`${mod.source}\n/* dependency-hashes */\n${depHashes}`);
  hashMemo.set(rel, h);
  return h;
}

for (const rel of modules.keys()) moduleHash(rel);

function hashedRel(rel, hash) {
  const ext = extname(rel);
  return posix(join(dirname(rel), `${basename(rel, ext)}.${hash}${ext}`));
}

const assets = {};

for (const sourceRel of cssSources) {
  const abs = join(repoRoot, sourceRel);
  const source = readFileSync(abs, "utf8");
  const outRel = hashedRel(sourceRel, sha(source));
  writeFileSync(join(repoRoot, outRel), source, "utf8");
  assets[sourceRel] = publicUrl(outRel);
}

function rewriteModule(mod) {
  return mod.source.replace(importFromRe, (full, spec) => {
    if (!spec.startsWith(".")) return full;
    const depRel = repoPath(join(dirname(mod.abs), spec));
    const depHash = hashMemo.get(depRel);
    if (!depHash) return full;
    const depOutAbs = join(repoRoot, hashedRel(depRel, depHash));
    const nextSpec = posix(relative(dirname(join(repoRoot, hashedRel(mod.rel, hashMemo.get(mod.rel)))), depOutAbs));
    const normalized = nextSpec.startsWith(".") ? nextSpec : `./${nextSpec}`;
    return full.replace(spec, normalized);
  });
}

for (const mod of modules.values()) {
  const outRel = hashedRel(mod.rel, hashMemo.get(mod.rel));
  mkdirSync(dirname(join(repoRoot, outRel)), { recursive: true });
  writeFileSync(join(repoRoot, outRel), rewriteModule(mod), "utf8");
  assets[mod.rel] = publicUrl(outRel);
}

const manifest = {
  version: "3.1.0",
  generated_at: "deterministic",
  entrypoints: {
    app: assets[entry],
    styles: cssSources.map((rel) => assets[rel]),
  },
  assets,
};

writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf8");
console.log(`wrote ${repoPath(manifestPath)} with ${Object.keys(assets).length} assets`);
