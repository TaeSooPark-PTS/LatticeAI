#!/usr/bin/env node
import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const args = process.argv.slice(2);

if (args.length === 0) {
  console.error("usage: node scripts/run_python.mjs <python-args...>");
  process.exit(2);
}

const candidates = [
  process.env.LTCAI_PYTHON,
  path.join(process.cwd(), ".venv", "bin", "python"),
  path.join(process.cwd(), ".venv", "Scripts", "python.exe"),
  "python3",
  "python",
].filter(Boolean);

let lastError = null;

for (const candidate of candidates) {
  const isPath = candidate.includes(path.sep);
  if (isPath && !existsSync(candidate)) {
    continue;
  }

  const result = spawnSync(candidate, args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: "inherit",
  });

  if (result.error) {
    if (result.error.code === "ENOENT") {
      lastError = result.error;
      continue;
    }
    throw result.error;
  }

  process.exit(result.status ?? 1);
}

console.error(`No usable Python executable found. Last error: ${lastError?.message ?? "none"}`);
process.exit(127);
