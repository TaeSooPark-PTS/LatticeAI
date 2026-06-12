#!/usr/bin/env node
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { spawnSync } from "node:child_process";

const repo = join(import.meta.dirname, "..");
const frontend = join(repo, "frontend");
const staticRoot = join(repo, "static");
let failures = 0;

function fail(message) {
  failures += 1;
  console.error(`FAIL ${message}`);
}

function walk(dir, exts) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (name === "node_modules") continue;
      out.push(...walk(path, exts));
    } else if (exts.some((ext) => name.endsWith(ext))) {
      out.push(path);
    }
  }
  return out;
}

const tsc = spawnSync("npx", ["tsc", "-p", "tsconfig.json", "--noEmit"], { cwd: repo, encoding: "utf8" });
if (tsc.status !== 0) fail(`frontend typecheck\n${tsc.stdout}${tsc.stderr}`);
else console.log("typecheck: frontend TS passes");

const files = [
  ...walk(frontend, [".ts", ".tsx", ".css", ".html"]),
  ...walk(join(staticRoot, "app"), [".js", ".css", ".html", ".json"]),
  join(staticRoot, "sw.js"),
  join(staticRoot, "manifest.json"),
].filter(existsSync);

const external = /https?:\/\/(fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)/;
const stale = /static\/v3|lint_v3|build_v3_assets/;
let scanned = 0;
for (const file of files) {
  scanned += 1;
  const text = readFileSync(file, "utf8");
  if (external.test(text)) fail(`${relative(repo, file)}: CDN reference`);
  if (stale.test(text)) fail(`${relative(repo, file)}: stale v3 frontend reference`);
}
console.log(`privacy: ${scanned} frontend/static files scanned`);

const client = readFileSync(join(frontend, "src", "api", "client.ts"), "utf8");
if (!client.includes("openapi-fetch") || !client.includes("./openapi")) {
  fail("frontend/src/api/client.ts must use the generated OpenAPI client");
}
if (!existsSync(join(frontend, "src", "api", "openapi.ts"))) {
  fail("generated OpenAPI types missing");
}

const openapi = JSON.parse(readFileSync(join(frontend, "openapi.json"), "utf8"));
const openapiPaths = Object.keys(openapi.paths || {});
const requiredPaths = [
  "/health",
  "/chat",
  "/api/graph",
  "/api/search/hybrid",
  "/api/knowledge-graph/export",
  "/api/memory/recall",
  "/agents/api/run",
  "/workflows/api/definitions",
  "/workspace/os",
  "/models",
  "/api/brain/storage",
  "/api/brain/storage/postgres/docker",
  "/api/brain/storage/migrate-postgres",
  "/api/knowledge-graph/archive",
  "/api/knowledge-graph/archive/inspect",
  "/api/knowledge-graph/archive/verify",
  "/api/knowledge-graph/archive/import",
  "/api/knowledge-graph/archive/restore",
  "/api/knowledge-graph/backup-health",
  "/admin/product-hardening",
];
if (openapiPaths.length < 300) fail(`OpenAPI path count too low: ${openapiPaths.length}`);
for (const path of requiredPaths) {
  if (!openapiPaths.includes(path)) fail(`OpenAPI path missing: ${path}`);
}
console.log(`api: generated OpenAPI schema exposes ${openapiPaths.length} paths`);

if (failures) {
  console.error(`\nfrontend lint: ${failures} failure(s)`);
  process.exit(1);
}
console.log("\nfrontend lint: all checks pass");
