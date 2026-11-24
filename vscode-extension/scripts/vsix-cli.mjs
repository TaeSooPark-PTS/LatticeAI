#!/usr/bin/env node
/**
 * Cross-platform VSIX package & publish helper (replaces Unix-only $npm_package_version).
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const extDir = path.resolve(import.meta.dirname, "..");
const pkg = JSON.parse(readFileSync(path.join(extDir, "package.json"), "utf8"));
const version = pkg.version;
const vsixPath = path.join("..", "dist", `ltcai-${version}.vsix`);

const action = process.argv[2];
const isWin = process.platform === "win32";
const bin = (name) => (isWin ? `${name}.cmd` : name);

if (action === "package") {
  execFileSync(bin("npx"), ["--no-install", "vsce", "package", "--out", vsixPath], {
    cwd: extDir,
    stdio: "inherit",
    shell: isWin,
  });
} else if (action === "publish-vscode") {
  execFileSync(bin("npx"), ["--no-install", "vsce", "publish", "--packagePath", vsixPath], {
    cwd: extDir,
    stdio: "inherit",
    shell: isWin,
  });
} else if (action === "publish-openvsx") {
  execFileSync(bin("npx"), ["--no-install", "ovsx", "publish", vsixPath], {
    cwd: extDir,
    stdio: "inherit",
    shell: isWin,
  });
} else {
  console.error("Usage: node ./scripts/vsix-cli.mjs <package|publish-vscode|publish-openvsx>");
  process.exit(1);
}
