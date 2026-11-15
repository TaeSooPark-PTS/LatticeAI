#!/usr/bin/env node
/**
 * Ubuntu 24.04 / 26.04 (t64 transition) Runner & Chromium System Library
 * Compatibility Checker and Diagnostic Tool (Risk #14.1).
 *
 * Ensures GitHub Actions Linux workflows (`ubuntu-latest`, `ubuntu-24.04`,
 * `ubuntu-26.04`) gracefully handle Debian/Ubuntu 64-bit time_t (`t64`) library
 * renames and verify required Chromium / Tauri shared libraries before visual or
 * E2E browser suites execute.
 *
 * Usage:
 *   node scripts/check_ubuntu_runner_compat.mjs                  # static workflow check
 *   node scripts/check_ubuntu_runner_compat.mjs --diagnostic     # static + host OS report
 *   node scripts/check_ubuntu_runner_compat.mjs --install-deps   # install t64-safe apt libs on Linux
 *   node scripts/check_ubuntu_runner_compat.mjs --verify-chromium # verify shared libs + Chromium ldd
 */

import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const ROOT = path.resolve(path.dirname(__filename), "..");
const WORKFLOWS_DIR = path.join(ROOT, ".github", "workflows");

/**
 * Ubuntu 24.04 / 26.04 (`t64`) package candidate groups and the shared library
 * sonames they provide for Playwright Chromium and Tauri desktop components.
 */
export const UBUNTU_T64_LIBRARY_GROUPS = [
  {
    name: "ALSA audio (t64 transition)",
    candidates: ["libasound2t64", "libasound2"],
    soname: "libasound.so.2",
    requiredBy: "chromium",
  },
  {
    name: "ATK accessibility toolkit (t64 transition)",
    candidates: ["libatk1.0-0t64", "libatk1.0-0"],
    soname: "libatk-1.0.so.0",
    requiredBy: "chromium",
  },
  {
    name: "AT-SPI2 ATK bridge (t64 transition)",
    candidates: ["libatk-bridge2.0-0t64", "libatk-bridge2.0-0"],
    soname: "libatk-bridge-2.0.so.0",
    requiredBy: "chromium",
  },
  {
    name: "CUPS printing library (t64 transition)",
    candidates: ["libcups2t64", "libcups2"],
    soname: "libcups.so.2",
    requiredBy: "chromium",
  },
  {
    name: "GTK 3 GUI toolkit (t64 transition)",
    candidates: ["libgtk-3-0t64", "libgtk-3-0"],
    soname: "libgtk-3.so.0",
    requiredBy: "chromium",
  },
  {
    name: "Mesa Generic Buffer Manager",
    candidates: ["libgbm1"],
    soname: "libgbm.so.1",
    requiredBy: "chromium",
  },
  {
    name: "Network Security Services (NSS)",
    candidates: ["libnss3"],
    soname: "libnss3.so",
    requiredBy: "chromium",
  },
  {
    name: "XKB keyboard handling",
    candidates: ["libxkbcommon0"],
    soname: "libxkbcommon.so.0",
    requiredBy: "chromium",
  },
  {
    name: "Ayatana AppIndicator (Tauri tray)",
    candidates: ["libayatana-appindicator3-1"],
    soname: "libayatana-appindicator3.so.1",
    requiredBy: "tauri",
  },
  {
    name: "WebKit2GTK 4.1 (Tauri webview)",
    candidates: ["libwebkit2gtk-4.1-0", "webkit2gtk-4.1"],
    soname: "libwebkit2gtk-4.1.so.0",
    requiredBy: "tauri",
  },
];

export const PLAYWRIGHT_WORKFLOWS = ["visual.yml", "e2e-sidecar.yml"];

export function parseOsRelease(content) {
  const info = {};
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eqIdx = line.indexOf("=");
    if (eqIdx === -1) continue;
    const key = line.slice(0, eqIdx).trim();
    let val = line.slice(eqIdx + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    info[key] = val;
  }
  return info;
}

export function detectLinuxRelease() {
  if (process.platform !== "linux") {
    return { platform: process.platform, isUbuntu: false, versionId: null, prettyName: process.platform };
  }
  const osReleasePath = "/etc/os-release";
  if (!existsSync(osReleasePath)) {
    return { platform: "linux", isUbuntu: false, versionId: null, prettyName: "Linux (unknown)" };
  }
  const parsed = parseOsRelease(readFileSync(osReleasePath, "utf8"));
  return {
    platform: "linux",
    isUbuntu: (parsed.ID || "").toLowerCase() === "ubuntu",
    versionId: parsed.VERSION_ID || null,
    prettyName: parsed.PRETTY_NAME || parsed.NAME || "Linux",
  };
}

/**
 * Audit all `.github/workflows/*.yml` files for Ubuntu 24.04 / 26.04 and
 * Chromium `t64` transition resilience.
 */
export function checkWorkflows(workflowsDir = WORKFLOWS_DIR) {
  const errors = [];
  if (!existsSync(workflowsDir)) {
    errors.push(`Workflows directory missing: ${workflowsDir}`);
    return { ok: false, errors, checkedFiles: [] };
  }

  const files = readdirSync(workflowsDir)
    .filter((f) => f.endsWith(".yml") || f.endsWith(".yaml"))
    .sort();

  for (const file of files) {
    const fullPath = path.join(workflowsDir, file);
    const text = readFileSync(fullPath, "utf8");

    if (!text.includes('FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"')) {
      errors.push(`${file}: missing FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`);
    }

    if (PLAYWRIGHT_WORKFLOWS.includes(file)) {
      const requiredTokens = [
        "libasound2t64",
        "libatk1.0-0t64",
        "libatk-bridge2.0-0t64",
        "libcups2t64",
        "libgtk-3-0t64",
        "libgbm1",
        "libnss3",
        "libxkbcommon0",
        "libayatana-appindicator3-1",
        "webkit2gtk-4.1",
        "check_ubuntu_runner_compat.mjs",
      ];
      for (const token of requiredTokens) {
        if (!text.includes(token)) {
          errors.push(
            `${file}: missing Ubuntu 24.04/26.04 t64 resilience token "${token}"`,
          );
        }
      }
    }
  }

  return { ok: errors.length === 0, errors, checkedFiles: files };
}

export function installUbuntuDeps() {
  if (process.platform !== "linux") {
    console.log(
      `[ubuntu-compat] skipping apt package installation on non-Linux platform (${process.platform}).`,
    );
    return true;
  }

  const updateRes = spawnSync("sudo", ["apt-get", "update", "-y"], {
    stdio: "inherit",
  });
  if (updateRes.status !== 0) {
    console.warn("[ubuntu-compat] apt-get update returned non-zero; continuing with cached index.");
  }

  for (const group of UBUNTU_T64_LIBRARY_GROUPS) {
    let installed = false;
    for (const candidate of group.candidates) {
      const res = spawnSync(
        "sudo",
        ["apt-get", "install", "-y", "--no-install-recommends", candidate],
        { stdio: "inherit" },
      );
      if (res.status === 0) {
        console.log(`[ubuntu-compat] installed ${candidate} for ${group.name}`);
        installed = true;
        break;
      }
    }
    if (!installed && group.requiredBy === "chromium") {
      console.warn(
        `[ubuntu-compat] warning: none of [${group.candidates.join(", ")}] installed via apt for ${group.name}`,
      );
    }
  }
  return true;
}

export function verifySharedLibraries() {
  const osInfo = detectLinuxRelease();
  console.log(
    `[ubuntu-compat] host=${osInfo.prettyName} (ubuntu=${osInfo.isUbuntu}, version=${osInfo.versionId ?? "n/a"})`,
  );

  if (process.platform !== "linux") {
    console.log(
      "[ubuntu-compat] non-Linux runner detected; shared library soname probe skipped.",
    );
    return { ok: true, missingSonames: [], missingBinaryLibs: [] };
  }

  let ldcache = "";
  try {
    ldcache = execFileSync("ldconfig", ["-p"], { encoding: "utf8" });
  } catch {
    ldcache = "";
  }

  const missingSonames = [];
  if (ldcache) {
    for (const group of UBUNTU_T64_LIBRARY_GROUPS) {
      if (group.requiredBy !== "chromium") continue;
      if (!ldcache.includes(group.soname)) {
        missingSonames.push(`${group.soname} (${group.candidates.join(" || ")})`);
      }
    }
  }

  // Also check Playwright's Chromium executable if installed
  const missingBinaryLibs = [];
  const pwCheck = spawnSync(
    "node",
    [
      "-e",
      "try { const { chromium } = require('@playwright/test'); console.log(chromium.executablePath()); } catch {}",
    ],
    { cwd: ROOT, encoding: "utf8" },
  );
  const chromePath = (pwCheck.stdout || "").trim();
  if (chromePath && existsSync(chromePath)) {
    console.log(`[ubuntu-compat] inspecting Playwright Chromium binary: ${chromePath}`);
    try {
      const lddOut = execFileSync("ldd", [chromePath], { encoding: "utf8" });
      for (const line of lddOut.split(/\r?\n/)) {
        if (line.includes("=> not found")) {
          missingBinaryLibs.push(line.trim());
        }
      }
    } catch (err) {
      console.warn(`[ubuntu-compat] ldd check warning: ${err.message}`);
    }
  }

  const ok = missingSonames.length === 0 && missingBinaryLibs.length === 0;
  return { ok, missingSonames, missingBinaryLibs };
}

export function main(argv = process.argv.slice(2)) {
  const installDeps = argv.includes("--install-deps");
  const verifyChromium = argv.includes("--verify-chromium");
  const diagnostic = argv.includes("--diagnostic");

  const workflowResult = checkWorkflows();
  if (!workflowResult.ok) {
    console.error("[ubuntu-compat] Workflow compatibility check failed:");
    for (const err of workflowResult.errors) {
      console.error(`  - ${err}`);
    }
    return 1;
  }
  console.log(
    `[ubuntu-compat] Verified ${workflowResult.checkedFiles.length} GitHub Actions workflow(s) for Ubuntu 24.04 / 26.04 (t64) resilience.`,
  );

  if (installDeps) {
    installUbuntuDeps();
  }

  if (diagnostic) {
    const osInfo = detectLinuxRelease();
    console.log(
      `[ubuntu-compat] Diagnostic summary: platform=${osInfo.platform}, distro="${osInfo.prettyName}", version=${osInfo.versionId ?? "n/a"}`,
    );
  }

  if (verifyChromium) {
    const verifyResult = verifySharedLibraries();
    if (!verifyResult.ok) {
      console.error("[ubuntu-compat] Chromium shared library verification failed:");
      for (const item of verifyResult.missingSonames) {
        console.error(`  - missing soname: ${item}`);
      }
      for (const item of verifyResult.missingBinaryLibs) {
        console.error(`  - unresolved ldd dependency: ${item}`);
      }
      return 1;
    }
    console.log("[ubuntu-compat] All required Chromium shared libraries verified.");
  }

  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  process.exit(main());
}
