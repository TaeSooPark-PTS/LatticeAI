#!/usr/bin/env node
import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";

const repo = join(import.meta.dirname, "..");
const version = process.argv[2] || process.env.npm_package_version;

if (!version || !/^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$/.test(version)) {
  console.error("usage: node scripts/clean_release_artifacts.mjs <version>");
  process.exit(2);
}

const targets = [
  join(repo, "dist", `ltcai-${version}-py3-none-any.whl`),
  join(repo, "dist", `ltcai-${version}.tar.gz`),
  join(repo, "dist", `ltcai-${version}.vsix`),
  join(repo, `ltcai-${version}.tgz`),
  join(repo, "src-tauri", "target", "release", "bundle", "dmg", `Lattice AI_${version}_aarch64.dmg`),
  join(repo, "src-tauri", "target", "release", "bundle", "macos", "Lattice AI.app"),
];

for (const target of targets) {
  if (existsSync(target)) {
    rmSync(target, { recursive: true, force: true });
    console.log(`removed ${target}`);
  }
}
