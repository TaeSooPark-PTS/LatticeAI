#!/usr/bin/env node
/**
 * Parallel Rust workspace test runner.
 *
 * Uses `cargo nextest run` when `cargo-nextest` is installed (fast process-isolated
 * parallel execution) and falls back cleanly to `cargo test --workspace` otherwise.
 * Always scrubs ambient connector/bot tokens (`LATTICEAI_TELEGRAM_BOT_TOKEN`, etc.)
 * so local developer shells never contaminate golden fixture parity suites.
 */
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifestPath = join(repoRoot, "rust", "Cargo.toml");

const cleanEnv = { ...process.env };
for (const key of [
  "LATTICEAI_TELEGRAM_BOT_TOKEN",
  "TELEGRAM_BOT_TOKEN",
  "LATTICEAI_ENABLE_TELEGRAM",
  "LATTICEAI_ENABLE_EXTERNAL_CONNECTORS",
]) {
  delete cleanEnv[key];
}

const extraArgs = process.argv.slice(2);
const hasNextest =
  spawnSync("cargo", ["nextest", "--version"], { stdio: "ignore", env: cleanEnv }).status === 0;

if (hasNextest) {
  console.log("[rust:test] running parallel suite via cargo-nextest");
  const res = spawnSync(
    "cargo",
    [
      "nextest",
      "run",
      "--manifest-path",
      manifestPath,
      "--workspace",
      "--no-fail-fast",
      "--status-level",
      "fail",
      "--final-status-level",
      "fail",
      ...extraArgs,
    ],
    { stdio: "inherit", env: cleanEnv },
  );
  if (res.status !== 0) process.exit(res.status ?? 1);
  // nextest does not execute doctests by default; run doctests separately.
  const docRes = spawnSync(
    "cargo",
    ["test", "--manifest-path", manifestPath, "--workspace", "--doc", "--quiet"],
    { stdio: "inherit", env: cleanEnv },
  );
  process.exit(docRes.status ?? 1);
}

console.log("[rust:test] cargo-nextest not found; falling back to cargo test --workspace");
const fallback = spawnSync(
  "cargo",
  ["test", "--manifest-path", manifestPath, "--workspace", ...extraArgs],
  { stdio: "inherit", env: cleanEnv },
);
process.exit(fallback.status ?? 1);
