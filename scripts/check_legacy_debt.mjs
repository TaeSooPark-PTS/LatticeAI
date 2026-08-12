#!/usr/bin/env node
// Legacy debt gate (9.9.1): the root shim layer stays removed.
//
// Fails when:
//  1. a Python module other than server.py appears at the repository root;
//  2. any source tree imports a removed root shim module;
//  3. a removed shim directory (tools/) reappears at the root.
//
// Mirror of tests/unit/test_legacy_root_shims.py, but static and runnable in
// `npm run lint` without a Python environment.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const repo = join(import.meta.dirname, "..");

const ALLOWED_ROOT_MODULES = new Set(["server.py"]);
const REMOVED_ROOT_DIRS = ["tools"];
const REMOVED_MODULES = [
  "ltcai_cli",
  "auto_setup",
  "setup_wizard",
  "mcp_registry",
  "kg_schema",
  "knowledge_graph",
  "knowledge_graph_api",
  "local_knowledge_api",
  "llm_router",
  "p_reinforce",
  "telegram_bot",
  "tools",
];
// Trees that must not import removed shims. static/app and node_modules are
// build output; src-tauri/target is compiler output.
const SOURCE_TREES = ["latticeai", "lattice_brain", "tests", "scripts", "bin"];

const failures = [];

for (const entry of readdirSync(repo)) {
  if (entry.endsWith(".py") && !ALLOWED_ROOT_MODULES.has(entry)) {
    failures.push(`root module reappeared: ${entry} — put code in latticeai/ or lattice_brain/`);
  }
}
for (const dir of REMOVED_ROOT_DIRS) {
  if (existsSync(join(repo, dir))) {
    failures.push(`removed root package reappeared: ${dir}/ — use latticeai.${dir} instead`);
  }
}

const modulePattern = REMOVED_MODULES.join("|");
const pyImport = new RegExp(`^\\s*(?:import\\s+(?:${modulePattern})\\b|from\\s+(?:${modulePattern})\\s+import\\b)`);
const jsSpawn = new RegExp(`(?:${modulePattern})\\.py\\b`);

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    if (entry === "__pycache__" || entry === "node_modules" || entry === ".venv") continue;
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) yield* walk(full);
    else yield full;
  }
}

for (const tree of SOURCE_TREES) {
  const base = join(repo, tree);
  if (!existsSync(base)) continue;
  for (const file of walk(base)) {
    const rel = relative(repo, file);
    if (/\.py$/.test(file)) {
      const lines = readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        if (pyImport.test(line)) {
          failures.push(`${rel}:${i + 1} imports a removed root shim: ${line.trim()}`);
        }
      });
    } else if (/\.(mjs|cjs|js)$/.test(file) && !/\.min\.js$/.test(file)) {
      const lines = readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        if (jsSpawn.test(line) && !line.includes("check_legacy_debt")) {
          failures.push(`${rel}:${i + 1} references a removed root shim file: ${line.trim()}`);
        }
      });
    }
  }
}

if (failures.length > 0) {
  console.error("legacy debt gate FAILED:");
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log("legacy debt gate ok: root shim layer stays removed (server.py only)");
