#!/usr/bin/env node
/* Lattice v3 frontend lint. Gates `npm run lint`:
 *  1. Syntax-check every v3 ES module (node --check).
 *  2. Design tokens: no raw hex/rgb colors in static/v3/css outside the two
 *     token files (lattice.tokens.css, tokens.css) — themed surfaces must use
 *     var(--…) tokens.
 *  3. No inline style colors in view JS (style="…color: #…" or
 *     style.color = "#…" literals).
 *  4. Privacy: zero CDN/external URLs in shipped static HTML/CSS/JS —
 *     fonts/icons/libs are vendored under static/vendor.
 *  5. i18n: routes, shell, and new v4 parity views must use the SPA i18n
 *     runtime instead of inert localStorage-only language toggles.
 * Exits non-zero on any failure. */
import { readdirSync, statSync, readFileSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..");
const v3js = join(repo, "static", "v3", "js");
const v3css = join(repo, "static", "v3", "css");
const staticRoot = join(repo, "static");

function walk(dir, ext) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p, ext));
    else if (ext.some((e) => name.endsWith(e))) out.push(p);
  }
  return out;
}

let failed = 0;
const fail = (msg) => { failed++; console.error(`FAIL ${msg}`); };

// ── 1. syntax ────────────────────────────────────────────────────────────
const modules = walk(v3js, [".js"]).sort();
let syntaxOk = 0;
for (const file of modules) {
  const r = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
  if (r.status === 0) syntaxOk++;
  else fail(`${relative(repo, file)}\n${r.stderr || r.stdout}`);
}
console.log(`syntax: ${syntaxOk}/${modules.length} v3 modules pass`);

// ── 2. raw colors in v3 css (outside token files) ────────────────────────
const TOKEN_FILES = new Set(["lattice.tokens.css", "tokens.css"]);
const colorRe = /#[0-9a-fA-F]{3,8}\b|rgba?\(/;
let cssChecked = 0;
for (const file of walk(v3css, [".css"]).sort()) {
  const base = file.split("/").pop();
  if (TOKEN_FILES.has(base) || /\.[0-9a-f]{8}\.css$/.test(base)) continue; // tokens + hashed builds
  cssChecked++;
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    const code = line.split("/*")[0];
    // mask-image gradients use #000/transparent as ALPHA values, not themed
    // colors — they are theme-independent and exempt.
    if (/mask-image|-webkit-mask/.test(code)) return;
    if (colorRe.test(code)) fail(`${relative(repo, file)}:${i + 1} raw color (use a var(--…) token): ${line.trim().slice(0, 90)}`);
  });
}
console.log(`tokens: ${cssChecked} non-token v3 css files scanned for raw colors`);

// ── 3. inline style colors in view JS ────────────────────────────────────
const inlineColorRe = /style\s*=\s*["'`][^"'`]*(?:color|background)\s*:\s*(#|rgb)/i;
const styleAssignRe = /\.style\.(color|background(?:Color)?)\s*=\s*["'`](#|rgb)/i;
let jsChecked = 0;
for (const file of modules) {
  if (/\.[0-9a-f]{8}\.js$/.test(file)) continue; // hashed builds mirror sources
  jsChecked++;
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    if (inlineColorRe.test(line) || styleAssignRe.test(line)) {
      fail(`${relative(repo, file)}:${i + 1} inline style color (use a token/class): ${line.trim().slice(0, 90)}`);
    }
  });
}
console.log(`inline-style: ${jsChecked} v3 source modules scanned`);

// ── 4. no CDN/external asset URLs in shipped static files ────────────────
const cdnRe = /https?:\/\/(fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)/;
let shippedChecked = 0;
for (const file of walk(staticRoot, [".html", ".css", ".js"]).sort()) {
  if (file.includes(`${join("static", "vendor")}`)) continue; // vendored copies may cite origins in comments
  shippedChecked++;
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    if (cdnRe.test(line)) fail(`${relative(repo, file)}:${i + 1} CDN reference (vendor it under static/vendor): ${line.trim().slice(0, 90)}`);
  });
}
console.log(`privacy: ${shippedChecked} shipped static files scanned for CDN URLs`);

// ── 5. i18n acceptance for T9 surfaces ──────────────────────────────────
const i18nRequired = [
  "static/v3/js/core/routes.js",
  "static/v3/js/core/shell.js",
  "static/v3/js/views/account.js",
  "static/v3/js/views/workspace-admin.js",
  "static/v3/js/views/snapshots.js",
  "static/v3/js/views/activity.js",
  "static/v3/js/views/runs.js",
  "static/v3/js/views/network.js",
];
let i18nChecked = 0;
for (const rel of i18nRequired) {
  const file = join(repo, rel);
  const text = readFileSync(file, "utf8");
  i18nChecked++;
  if (!/i18n\.js/.test(text)) fail(`${rel}: missing i18n runtime import`);
  if (!/\bt\(/.test(text)) fail(`${rel}: no translation lookup found`);
}
console.log(`i18n: ${i18nChecked} route/shell/parity modules checked`);

if (failed) {
  console.error(`\nv3 frontend lint: ${failed} failure(s)`);
  process.exit(1);
}
console.log(`\nv3 frontend lint: all checks pass`);
