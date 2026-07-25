#!/usr/bin/env node
/**
 * Start an isolated Lattice sidecar and run Playwright first-value E2E
 * against it (Wave 3.2 residual / v9.9.5).
 *
 * Mirrors scripts/run_integration_tests.mjs isolation rules: never touch
 * the developer's real HOME / ~/.ltcai / Brain vault.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const host = process.env.LTCAI_E2E_HOST || "127.0.0.1";
if (!new Set(["127.0.0.1", "localhost"]).has(host)) {
  throw new Error(`LTCAI_E2E_HOST must be loopback, received: ${host}`);
}
const port = process.env.LTCAI_E2E_PORT || "4899";
if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error(`LTCAI_E2E_PORT must be 1–65535, received: ${port}`);
}
const baseUrl = `http://${host}:${port}`;
const venvPython = join(process.cwd(), ".venv", "bin", "python");
const python = process.env.PYTHON || (existsSync(venvPython) ? venvPython : "python3");

const sandboxRoot = mkdtempSync(join(tmpdir(), "ltcai-e2e-"));
const sandbox = {
  home: join(sandboxRoot, "home"),
  data: join(sandboxRoot, "data"),
  brain: join(sandboxRoot, "brain"),
  agent: join(sandboxRoot, "agent-workspace"),
  vault: join(sandboxRoot, "vault"),
  cache: join(sandboxRoot, "cache"),
  config: join(sandboxRoot, "config"),
  tmp: join(sandboxRoot, "tmp"),
};
for (const path of Object.values(sandbox)) mkdirSync(path, { recursive: true });

const isolatedEnv = {
  ...process.env,
  HOME: sandbox.home,
  USERPROFILE: sandbox.home,
  XDG_CACHE_HOME: sandbox.cache,
  XDG_CONFIG_HOME: sandbox.config,
  XDG_DATA_HOME: join(sandboxRoot, "share"),
  TMPDIR: sandbox.tmp,
  TEMP: sandbox.tmp,
  TMP: sandbox.tmp,
  LATTICEAI_MODE: "local",
  LATTICEAI_HOST: host,
  LATTICEAI_PORT: port,
  LATTICEAI_DATA_DIR: sandbox.data,
  LATTICEAI_BRAIN_DIR: sandbox.brain,
  LATTICEAI_AGENT_ROOT: sandbox.agent,
  LATTICEAI_OBSIDIAN_VAULT_DIR: sandbox.vault,
  LATTICEAI_STORAGE_ENGINE: "sqlite",
  LATTICEAI_POSTGRES_DSN: "",
  LATTICEAI_REQUIRE_AUTH: "false",
  LATTICEAI_ENABLE_TELEGRAM: "false",
  LATTICEAI_AUTOLOAD_MODELS: "false",
  LATTICEAI_ALLOW_MODEL_DOWNLOADS: "false",
  LATTICEAI_AUTO_READ_CHAT_PATHS: "false",
  LATTICEAI_TUNNEL: "false",
  LATTICEAI_DISCORD_PERMISSION_WEBHOOK: "",
  LATTICEAI_DISCORD_BOT_TOKEN: "",
  OIDC_DISCOVERY_URL: "",
  PYTHON_KEYRING_BACKEND: "keyring.backends.null.Keyring",
  LTCAI_E2E_BASE_URL: baseUrl,
};

let sandboxCleaned = false;
function cleanupSandbox() {
  if (sandboxCleaned) return;
  sandboxCleaned = true;
  rmSync(sandboxRoot, { recursive: true, force: true });
}
process.once("exit", cleanupSandbox);

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealth(url, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${url}/health`, { signal: AbortSignal.timeout(3000) });
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error?.message || error);
    }
    await delay(500);
  }
  throw new Error(`Timed out waiting for ${url}/health: ${lastError}`);
}

function stop(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }, 5000);
    child.once("close", () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill("SIGTERM");
  });
}

async function main() {
  console.log(`[e2e] sandbox ${sandboxRoot}`);
  console.log(`[e2e] starting sidecar at ${baseUrl}`);
  const server = spawn(
    python,
    ["-m", "latticeai.cli.entrypoint", "--host", host, "--port", port],
    { stdio: "inherit", env: isolatedEnv, cwd: process.cwd() },
  );

  let exitCode = 1;
  try {
    await waitForHealth(`${baseUrl}/health`);
    console.log("[e2e] sidecar healthy — running Playwright");
    const playwright = spawn(
      "npx",
      ["playwright", "test", "-c", "playwright.e2e.config.js"],
      { stdio: "inherit", env: isolatedEnv, cwd: process.cwd() },
    );
    exitCode = await new Promise((resolve, reject) => {
      playwright.on("error", reject);
      playwright.on("close", (code) => resolve(code ?? 1));
    });
  } catch (error) {
    console.error("[e2e] failed:", error);
    exitCode = 1;
  } finally {
    await stop(server);
    cleanupSandbox();
  }
  process.exit(exitCode);
}

main();
