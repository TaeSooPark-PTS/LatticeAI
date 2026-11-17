#!/usr/bin/env node
/**
 * Server-side i18n ratchet.
 *
 * Every string the API hands a person used to be a literal at the raise site,
 * written in whichever language the author happened to be thinking in. One user
 * could get `"사용자를 찾을 수 없습니다."` from one router and, two files over,
 * `"Knowledge Graph ingestion is disabled."` — half the product in a language
 * they never chose.
 *
 * `latticeai/core/messages.py` fixed the mechanism; this gate keeps the fix from
 * eroding. Routers listed in LOCALIZED below are done: they must raise through
 * `http_error(...)` / `translate(...)`, never a literal. Routers not yet listed
 * are honestly *not* claimed to be localized — adding one to the list is how a
 * migration is declared finished, and from then on it cannot slip back.
 *
 * Pass-throughs (`detail=str(exc)`, `detail=exc.to_detail()`, a variable) are
 * allowed: the wording belongs to the service that raised, and moving it here
 * would only relocate the problem.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const API_DIR = "latticeai/api";

// Routers whose user-facing details are in the message catalog. Append here
// when a router is migrated. A name is removed only when the router itself is
// deleted — never to quiet a failure — and the loop below turns a stale name
// into an error rather than a skip, so the two cases cannot be confused.
// `tools` left this list in v11.8.0 with `latticeai/api/tools.py`, whose two
// routes had no caller anywhere in the tree.
const LOCALIZED = [
  "agent_worker_seam",
  "health",
  "models",
  "search",
  "worker_compute",
  "worker_seams",
];

const failures = [];
const known = new Set(readdirSync(API_DIR).filter((name) => name.endsWith(".py")));

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
    // Anything that is not a quoted literal is a pass-through: `str(exc)`, a
    // variable, `translate(...)`, a dict. Those carry no wording of their own.
    if (!/^f?"/.test(value)) return;
    // A literal used as an argument to a string method — `"; ".join(errors)` —
    // is a separator, not a message.
    if (/^f?"[^"]*"\s*\./.test(value)) return;
    // An empty string carries no wording either.
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

const remaining = [...known].filter(
  (entry) => entry !== "__init__.py" && !LOCALIZED.includes(entry.replace(/\.py$/, "")),
).length;

// Rust canonical catalog check (v13.0.0): `rust/lattice-core/src/messages.rs`
// must keep sorted, non-empty `(id, ko, en)` entries and `lattice-auth` must
// delegate to `lattice_core::messages` instead of carrying a duplicate CATALOG.
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
  `Server i18n gate: ${LOCALIZED.length} Python router(s) + ${rustEntries.length} Rust catalog message(s) verified (${remaining} Python keep-set router(s) unclaimed).`,
);
