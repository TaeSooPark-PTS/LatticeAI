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
// when a router is migrated — never remove. Module names carry no extension:
// `check_legacy_debt.mjs` rejects a bare "<module>.py" literal in scripts/,
// which is how the removed root shims used to be referenced.
const LOCALIZED = [
  "agent_worker_seam",
  "models",
  "tools",
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
console.log(
  `Server i18n gate: ${LOCALIZED.length} router(s) localized and locked; `
  + `${remaining} not yet claimed.`,
);
