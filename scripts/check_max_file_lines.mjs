#!/usr/bin/env node
// Max file length gate (11.3.0): no first-party source file over 1,000 lines.
//
// Why a gate and not a habit: `frontend/src/styles.css` reached 10,956 lines and
// twenty-seven other files passed 1,000 before anyone noticed. Long files are
// not a style opinion here — they are slow to review, they burn an agent's
// context on the 90% of the file that is irrelevant to the change, and two
// people editing different features of the same file conflict every time.
//
// The rule: every git-tracked *.py *.ts *.tsx *.js *.mjs *.cjs *.css file — tests
// included — stays at or under 1,000 lines. Split by cohesion, not by cutting at
// line 1,000: a module that needs more than a thousand lines is doing more than
// one job.
//
// Exit 0 clean, 1 when a file is over the limit, 2 when the check cannot run.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import process from "node:process";

const repo = join(import.meta.dirname, "..");

const MAX_LINES = 1000;

const EXTENSIONS = ["*.py", "*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs", "*.css"];

// Paths that are not written by hand. Each entry names why it is here; nothing
// gets added to this list to make a hand-written file pass.
const EXCLUDED = [
  // Generated from the OpenAPI schema by `npm run frontend:openapi`.
  "frontend/src/api/openapi.ts",
  // Vite build output. Editing it by hand is already a mistake (v10.1.1).
  "static/app/",
  // Third-party libraries shipped as-is.
  "static/vendor/",
  // Tauri's generated platform scaffolding.
  "src-tauri/gen/",
];

function isExcluded(file) {
  return EXCLUDED.some((prefix) =>
    prefix.endsWith("/") ? file.startsWith(prefix) : file === prefix,
  );
}

/** `wc -l` semantics: how many newline-terminated lines the file holds. */
function lineCount(file) {
  const text = readFileSync(join(repo, file), "utf8");
  if (!text) return 0;
  const parts = text.split("\n");
  return text.endsWith("\n") ? parts.length - 1 : parts.length;
}

// `--others --exclude-standard` adds files that exist but are not committed yet
// while still honouring .gitignore. Without it a brand-new 3,000-line module
// passes the gate on the branch that introduces it and only fails after the
// commit that was supposed to be reviewed against it.
let tracked;
try {
  const listed = execFileSync(
    "git",
    ["ls-files", "--cached", "--others", "--exclude-standard", "--", ...EXTENSIONS],
    { cwd: repo, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
  );
  tracked = [...new Set(listed.split("\n").map((line) => line.trim()).filter(Boolean))];
} catch (error) {
  console.error(`max file lines: could not list tracked files (${error.message})`);
  process.exit(2);
}

const oversized = [];
for (const file of tracked) {
  if (isExcluded(file)) continue;
  let lines;
  try {
    lines = lineCount(file);
  } catch {
    // A tracked path that is not readable (deleted in the working tree, a
    // submodule pointer) is not this gate's business.
    continue;
  }
  if (lines > MAX_LINES) oversized.push({ file, lines });
}

if (oversized.length) {
  oversized.sort((a, b) => b.lines - a.lines);
  console.error(
    `max file lines: ${oversized.length} file(s) over ${MAX_LINES} lines.\n` +
      oversized.map(({ file, lines }) => `  ${String(lines).padStart(6)}  ${file}`).join("\n") +
      "\n\n  Split each into cohesive modules (target ≤700 lines) and re-run.\n" +
      "  Generated or vendored output belongs in the EXCLUDED list in\n" +
      "  scripts/check_max_file_lines.mjs, with a comment saying why.",
  );
  process.exit(1);
}

console.log(
  `max file lines ok: ${tracked.length} tracked file(s) scanned, none over ${MAX_LINES} lines`,
);
