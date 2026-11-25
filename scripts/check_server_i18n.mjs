#!/usr/bin/env node
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const API_DIR = "latticeai/api";

const LOCALIZED = [
  "agent_worker_seam",
  "health",
  "models",
  "search",
  "worker_compute",
  "worker_seams",
];

const NON_ROUTER_HELPERS = [
  "worker_compute_render",
  "worker_resilience",
];

const failures = [];
const known = new Set(readdirSync(API_DIR).filter((name) => name.endsWith(".py")));

for (const name of NON_ROUTER_HELPERS) {
  const file = `${name}.py`;
  if (!known.has(file)) {
    failures.push(`${file}: listed in NON_ROUTER_HELPERS but no such module exists`);
  }
  if (LOCALIZED.includes(name)) {
    failures.push(`${file}: listed in both LOCALIZED and NON_ROUTER_HELPERS`);
  }
}

for (const name of LOCALIZED) {
  const file = `${name}.py`;
  if (!known.has(file)) {
    failures.push(`${file}: listed as localized but no such router exists`);
    continue;
  }
  const lines = readFileSync(join(API_DIR, file), "utf8").split("\n");
  lines.forEach((line, index) => {
    const match = line.match(/detail\s*=\s*(.+)$/);
    if (!match) return;
    const value = match[1].trim();
    if (!/^f?"/.test(value)) return;
    if (/^f?"[^"]*"\s*\./.test(value)) return;
    if (/^f?"\s*"/.test(value)) return;
    failures.push(
      `${file}:${index + 1}: literal error detail — use http_error("<key>", …) `
      + `with a key in latticeai/core/messages.py\n      ${line.trim()}`,
    );
  });
}

if (failures.length) {
  console.error("Server i18n gate failed:\n");
  for (const failure of failures) console.error("  " + failure);
  console.error(
    `\n${failures.length} literal detail(s) in routers declared localized.`
    + "\nAdd the message to latticeai/core/messages.py (both ko and en) and raise"
    + "\nvia http_error(status, key, resolve_language(request)).",
  );
  process.exit(1);
}

const unclaimed = [...known]
  .filter((entry) => entry !== "__init__.py")
  .map((entry) => entry.replace(/\.py$/, ""))
  .filter((name) => !LOCALIZED.includes(name) && !NON_ROUTER_HELPERS.includes(name))
  .sort();

for (const name of unclaimed) {
  failures.push(
    `${name}.py: unclaimed module in ${API_DIR}/ — localize its user-facing errors and add to LOCALIZED, `
    + "or register it in NON_ROUTER_HELPERS if it defines no user-facing HTTP routes",
  );
}

const rustCoreMessages = readFileSync("rust/lattice-core/src/messages.rs", "utf8");
const rustEntries = [];
const tupleRegex = /^\s*\(\s*"([^"]+)"\s*,\s*"((?:\\.|[^"\\])*)"\s*,\s*"((?:\\.|[^"\\])*)"\s*\)\s*,/gm;
let tupleMatch;
while ((tupleMatch = tupleRegex.exec(rustCoreMessages)) !== null) {
  rustEntries.push({ id: tupleMatch[1], ko: tupleMatch[2], en: tupleMatch[3] });
}
if (rustEntries.length === 0) {
  failures.push("rust/lattice-core/src/messages.rs: failed to parse MESSAGES catalog entries");
}
for (let i = 0; i < rustEntries.length; i += 1) {
  const { id, ko, en } = rustEntries[i];
  if (!ko.trim() || !en.trim()) {
    failures.push(`rust/lattice-core/src/messages.rs: '${id}' must have non-empty ko and en strings`);
  }
  if (i > 0 && rustEntries[i - 1].id >= id) {
    failures.push(
      `rust/lattice-core/src/messages.rs: catalog out of binary-search sort order (` +
      `'${rustEntries[i - 1].id}' >= '${id}')`,
    );
  }
}
const rustAuthMessages = readFileSync("rust/lattice-auth/src/messages.rs", "utf8");
if (/const\s+CATALOG\b/.test(rustAuthMessages)) {
  failures.push(
    "rust/lattice-auth/src/messages.rs: duplicate CATALOG detected — delegate to lattice_core::messages",
  );
}

if (failures.length) {
  console.error("Server i18n gate failed:\n");
  for (const failure of failures) console.error("  " + failure);
  process.exit(1);
}

console.log(
  `Server i18n gate: ${LOCALIZED.length} Python router(s) + ${NON_ROUTER_HELPERS.length} non-router helper(s) + ${rustEntries.length} Rust catalog message(s) verified (${unclaimed.length} unclaimed).`,
);
