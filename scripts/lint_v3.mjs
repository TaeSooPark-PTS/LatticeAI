#!/usr/bin/env node
/* Syntax-check every Lattice AI v3 frontend ES module (node --check, ESM auto-detect).
 * Exits non-zero on the first failure so it can gate `npm run lint`. */
import { readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "static", "v3", "js");

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (name.endsWith(".js")) out.push(p);
  }
  return out;
}

const files = walk(root).sort();
let failed = 0;
for (const file of files) {
  const r = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
  if (r.status === 0) {
    console.log(`ok   ${file.replace(root, "static/v3/js")}`);
  } else {
    failed++;
    console.error(`FAIL ${file.replace(root, "static/v3/js")}\n${r.stderr || r.stdout}`);
  }
}
console.log(`\nv3 frontend: ${files.length - failed}/${files.length} modules pass`);
process.exit(failed ? 1 : 0);
