#!/usr/bin/env node
/**
 * Build the VS Code extension VSIX into `dist/ltcai-<version>.vsix`.
 *
 * Why a script instead of an inline `cd vscode-extension && npm run package:vsix`:
 *
 *  1. **Single version source.** The output name is derived from the ROOT
 *     `package.json` version — the same value `release:validate` checks. The old
 *     inline form used the extension's own `$npm_package_version`, so a version
 *     drift between root and extension produced a `dist/ltcai-<ext>.vsix` that the
 *     validator (run from root) reported as a *missing* `dist/ltcai-<root>.vsix`.
 *  2. **Fresh-checkout / CI safe.** It installs the extension's toolchain
 *     (`tsc`, `vsce`) when `node_modules` is absent, so the artifact builds on a
 *     clean clone — not only on a warmed-up dev tree.
 *  3. **Fails loudly.** It verifies the compiled entrypoint and the final VSIX
 *     exist, exiting non-zero otherwise, so a skipped compile can't yield a
 *     silently-empty or missing artifact.
 *
 * Mirrors the tag-driven `.github/workflows/release.yml` VSIX step.
 */
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

// 1) Ensure the extension toolchain (tsc + vsce) is installed.
if (!existsSync(join(extDir, "node_modules", ".bin", `vsce${binExt}`))) {
  const installCmd = existsSync(join(extDir, "package-lock.json")) ? "ci" : "install";
  run(`npm${binExt}`, [installCmd, "--no-audit", "--no-fund"], extDir);
}

// 2) Compile and assert the entrypoint exists (vsce also runs vscode:prepublish,
//    but we fail fast and explicitly here for a clearer error).
run(`npm${binExt}`, ["run", "compile"], extDir);
if (!existsSync(join(extDir, "out", "extension.js"))) {
  console.error("build_vsix: vscode-extension/out/extension.js missing after compile");
  process.exit(1);
}

// 3) Package to the root-version-scoped path. `--no-yarn` matches release.yml.
run(join(extDir, "node_modules", ".bin", `vsce${binExt}`), ["package", "--no-yarn", "-o", outFile], extDir);

// 4) Verify the artifact landed where release:validate expects it.
if (!existsSync(outFile)) {
  console.error(`build_vsix: expected artifact not found: ${outFile}`);
  process.exit(1);
}
console.log(`build_vsix: wrote ${outFile.replace(repoRoot, ".")}`);
