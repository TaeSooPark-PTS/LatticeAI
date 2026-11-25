#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import process from "node:process";

const repo = join(import.meta.dirname, "..");

const MAX_LINES = 1000;

const EXTENSIONS = ["*.py", "*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs", "*.css", "*.rs"];

const EXCLUDED = [
  "frontend/src/api/openapi.ts",
  "static/app/",
  "static/vendor/",
  "src-tauri/gen/",
];

function isExcluded(file) {
  return EXCLUDED.some((prefix) =>
    prefix.endsWith("/") ? file.startsWith(prefix) : file === prefix,
  );
}

function lineCount(file) {
  const text = readFileSync(join(repo, file), "utf8");
  if (!text) return 0;
  const parts = text.split("\n");
  return text.endsWith("\n") ? parts.length - 1 : parts.length;
}

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
