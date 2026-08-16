#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const tempRoot = mkdtempSync(join(tmpdir(), "ltcai-openapi-check-"));
const generatedSchema = join(tempRoot, "openapi.json");
const generatedTypes = join(tempRoot, "openapi.ts");

function runNode(script, args) {
  const result = spawnSync(process.execPath, [script, ...args], {
    cwd: repo,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${script} exited with status ${result.status ?? "unknown"}`);
  }
}

function matches(left, right) {
  return readFileSync(left).equals(readFileSync(right));
}

try {
  // P1 cutover: the worker spec is 19 routes since v11.8.0 deleted nine that
  // had no caller. The committed contract is the composition of fragments +
  // that worker spec, not the worker dump itself.
  const workerSpec = join(tempRoot, "worker.json");
  runNode(join(repo, "scripts", "run_python.mjs"), [
    "scripts/export_openapi.py",
    workerSpec,
  ]);
  runNode(join(repo, "scripts", "run_python.mjs"), [
    "scripts/compose_openapi.py",
    "--worker-spec",
    workerSpec,
    "--output",
    generatedSchema,
  ]);
  runNode(join(repo, "node_modules", "openapi-typescript", "bin", "cli.js"), [
    generatedSchema,
    "-o",
    generatedTypes,
  ]);

  const committedSchema = join(repo, "frontend", "openapi.json");
  const committedTypes = join(repo, "frontend", "src", "api", "openapi.ts");
  const drift = [];
  if (!matches(generatedSchema, committedSchema)) drift.push("frontend/openapi.json");
  if (!matches(generatedTypes, committedTypes)) drift.push("frontend/src/api/openapi.ts");

  if (drift.length) {
    console.error("OpenAPI generated artifacts are stale:");
    for (const path of drift) console.error(`- ${path}`);
    console.error("Run `npm run frontend:openapi` and commit both generated files.");
    process.exitCode = 1;
  } else {
    console.log("OpenAPI schema and TypeScript client are synchronized.");
  }
} finally {
  rmSync(tempRoot, { recursive: true, force: true });
}
