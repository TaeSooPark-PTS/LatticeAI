#!/usr/bin/env node
// Proves the lazy i18n split is safe.
//
// `frontend/src/i18n/*` used to be one synchronous table merged into the entry
// chunk, so every route's copy sat on the first-paint path (~3,000 lines).
// Namespaces now register themselves on import, and a route pulls the namespace
// it needs into its own lazy chunk.
//
// The failure mode that buys is silent: a component reads a key whose namespace
// its chunk never imported, `t()` falls through to returning the raw key, and
// the UI renders "system.permission.title" instead of Korean text. No error, no
// test failure unless that exact component is rendered in a test.
//
// So this check walks the real module graph. For each entry — the eager root
// plus every React.lazy() boundary — it collects the static closure, every
// `t(lang, "key")` used in it, and every i18n namespace it imports. A key whose
// namespace is not reachable from that entry is an error.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, resolve, relative } from "node:path";
import process from "node:process";

const repo = join(import.meta.dirname, "..");
const src = join(repo, "frontend", "src");
const i18nDir = join(src, "i18n");

const NAMESPACES = readdirSync(i18nDir)
  .filter((name) => name.endsWith(".ts"))
  .map((name) => name.replace(/\.ts$/, ""))
  .filter((name) => !["types", "registry"].includes(name));

// key -> namespace that defines it
const keyOwner = new Map();
for (const ns of NAMESPACES) {
  const text = readFileSync(join(i18nDir, `${ns}.ts`), "utf8");
  for (const match of text.matchAll(/^\s+"([^"]+)":/gm)) keyOwner.set(match[1], ns);
}

// `shell` is registered by i18n.ts itself, so it is always available.
const ALWAYS_AVAILABLE = new Set(["shell"]);

const STATIC_IMPORT = /^\s*import\s+(?!type\s)([^;]*?)from\s+"([^"]+)"/gm;
const BARE_IMPORT = /^\s*import\s+"([^"]+)"/gm;
const LAZY_IMPORT = /import\(\s*"([^"]+)"\s*\)/g;
const KEY_USE = /\bt\(\s*[A-Za-z_$][\w.$]*\s*,\s*"([^"]+)"/g;

function resolveSpec(spec, importer) {
  let base;
  if (spec.startsWith("@/")) base = join(src, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(importer), spec);
  else return null;
  for (const candidate of [`${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function readModule(file) {
  const raw = readFileSync(file, "utf8");
  // Drop `import { type X }`-only specifiers: TypeScript elides them, so they
  // are not runtime edges and must not count as namespace coverage.
  const statics = [];
  for (const match of raw.matchAll(STATIC_IMPORT)) {
    const clause = match[1];
    const named = clause.match(/\{([^}]*)\}/);
    const onlyTypes = named && named[1].trim().length > 0
      && named[1].split(",").every((part) => !part.trim() || part.trim().startsWith("type "))
      && !/^\s*\w+\s*,/.test(clause);
    if (!onlyTypes) statics.push(match[2]);
  }
  for (const match of raw.matchAll(BARE_IMPORT)) statics.push(match[1]);
  return {
    statics,
    lazy: [...raw.matchAll(LAZY_IMPORT)].map((m) => m[1]),
    keys: [...raw.matchAll(KEY_USE)].map((m) => m[1]),
  };
}

const cache = new Map();
function moduleInfo(file) {
  if (!cache.has(file)) cache.set(file, readModule(file));
  return cache.get(file);
}

/** Static closure of an entry, not crossing further lazy boundaries. */
function closure(entry) {
  const seen = new Set();
  const stack = [entry];
  const lazyEdges = new Set();
  while (stack.length) {
    const file = stack.pop();
    if (seen.has(file) || !existsSync(file)) continue;
    seen.add(file);
    const info = moduleInfo(file);
    for (const spec of info.lazy) {
      const target = resolveSpec(spec, file);
      if (target) lazyEdges.add(target);
    }
    for (const spec of info.statics) {
      const target = resolveSpec(spec, file);
      if (target) stack.push(target);
    }
  }
  return { modules: seen, lazyEdges };
}

const root = join(src, "main.tsx");
const entries = new Map([["<entry> main.tsx", root]]);
const rootClosure = closure(root);
for (const target of rootClosure.lazyEdges) {
  entries.set(`<lazy> ${relative(src, target)}`, target);
}

const errors = [];
for (const [label, entry] of entries) {
  const { modules } = closure(entry);
  const available = new Set(ALWAYS_AVAILABLE);
  for (const file of modules) {
    if (dirname(file) === i18nDir) {
      const ns = relative(i18nDir, file).replace(/\.ts$/, "");
      if (NAMESPACES.includes(ns)) available.add(ns);
    }
  }
  for (const file of modules) {
    if (dirname(file) === i18nDir) continue;
    for (const key of moduleInfo(file).keys) {
      const owner = keyOwner.get(key);
      if (!owner) {
        errors.push(`${label}: ${relative(src, file)} uses unknown key "${key}"`);
      } else if (!available.has(owner)) {
        errors.push(
          `${label}: ${relative(src, file)} uses "${key}" from the "${owner}" namespace, `
          + `which this chunk never imports (add: import "@/i18n/${owner}";)`,
        );
      }
    }
  }
}

if (errors.length) {
  console.error("i18n namespace coverage check failed:");
  for (const error of [...new Set(errors)].sort()) console.error(`- ${error}`);
  process.exit(1);
}

console.log(
  `i18n namespace coverage: ${entries.size} entry chunk(s) verified against `
  + `${NAMESPACES.length} namespaces (${keyOwner.size} keys).`,
);
