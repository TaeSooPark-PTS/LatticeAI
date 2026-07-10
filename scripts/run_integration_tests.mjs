#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const host = process.env.LTCAI_TEST_HOST || "127.0.0.1";
if (!new Set(["127.0.0.1", "localhost"]).has(host)) {
  throw new Error(`LTCAI_TEST_HOST must be loopback, received: ${host}`);
}
const port = process.env.LTCAI_TEST_PORT || "8899";
if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error(`LTCAI_TEST_PORT must be an integer from 1 to 65535, received: ${port}`);
}
// This runner owns the server lifecycle; never redirect its tests to a caller-
// supplied live server through LTCAI_TEST_BASE_URL.
const baseUrl = `http://${host}:${port}`;
const venvPython = join(process.cwd(), ".venv", "bin", "python");
const python = process.env.PYTHON || (existsSync(venvPython) ? venvPython : "python");

// Integration tests must never discover or mutate the developer's real HOME,
// ~/.ltcai, Brain vault, or agent workspace. Keep every user-state path under
// one disposable root and pass the exact same environment to the server and
// pytest process.
const sandboxRoot = mkdtempSync(join(tmpdir(), "ltcai-integration-"));
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
};

let sandboxCleaned = false;
function cleanupSandbox() {
  if (sandboxCleaned) return;
  sandboxCleaned = true;
  rmSync(sandboxRoot, { recursive: true, force: true });
}
process.once("exit", cleanupSandbox);

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      env: { ...isolatedEnv, ...options.env },
      cwd: options.cwd || process.cwd(),
    });
    child.on("error", reject);
    child.on("close", (code, signal) => resolve({ code, signal }));
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealth(url, timeoutMs = 30000) {
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

const server = spawn(
  python,
  ["-m", "uvicorn", "server:app", "--host", host, "--port", port],
  {
    cwd: process.cwd(),
    env: isolatedEnv,
    stdio: ["ignore", "pipe", "pipe"],
  },
);

server.stdout.on("data", (chunk) => process.stdout.write(chunk));
server.stderr.on("data", (chunk) => process.stderr.write(chunk));

let exitCode = 1;
try {
  await waitForHealth(baseUrl);
  const result = await run(python, ["-m", "pytest", "tests/integration/", "-v"], {
    env: { LTCAI_TEST_BASE_URL: baseUrl },
  });
  exitCode = result.code || 0;
} catch (error) {
  console.error(String(error?.message || error));
} finally {
  await stop(server);
  cleanupSandbox();
}

process.exit(exitCode);
