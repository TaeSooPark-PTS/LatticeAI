#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const extDir = join(repoRoot, "vscode-extension");
const distDir = join(repoRoot, "dist");

const version = JSON.parse(readFileSync(join(repoRoot, "package.json"), "utf8")).version;
const extVersion = JSON.parse(readFileSync(join(extDir, "package.json"), "utf8")).version;
if (extVersion !== version) {
  console.error(
    `build_vsix: version drift — root package.json is ${version} but ` +
      `vscode-extension/package.json is ${extVersion}. Bump both to the same value.`
  );
  process.exit(1);
}

const outFile = join(distDir, `ltcai-${version}.vsix`);
const binExt = process.platform === "win32" ? ".cmd" : "";

mkdirSync(distDir, { recursive: true });

function run(cmd, args, cwd) {
  console.log(`$ (cd ${cwd && cwd.replace(repoRoot, ".")}) ${cmd} ${args.join(" ")}`);
  execFileSync(cmd, args, { cwd, stdio: "inherit" });
}

if (!existsSync(join(extDir, "node_modules", ".bin", `vsce${binExt}`))) {
  const installCmd = existsSync(join(extDir, "package-lock.json")) ? "ci" : "install";
  run(`npm${binExt}`, [installCmd, "--no-audit", "--no-fund"], extDir);
}

run(`npm${binExt}`, ["run", "compile"], extDir);
if (!existsSync(join(extDir, "out", "extension.js"))) {
  console.error("build_vsix: vscode-extension/out/extension.js missing after compile");
  process.exit(1);
}

run(join(extDir, "node_modules", ".bin", `vsce${binExt}`), ["package", "--no-yarn", "-o", outFile], extDir);

if (!existsSync(outFile)) {
  console.error(`build_vsix: expected artifact not found: ${outFile}`);
  process.exit(1);
}
console.log(`build_vsix: wrote ${outFile.replace(repoRoot, ".")}`);
